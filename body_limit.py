"""Bound the complete ASGI body before multipart parsing or JSON decoding."""
from starlette.responses import JSONResponse
from tempfile import SpooledTemporaryFile


class BodyLimit:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ['POST','PUT','PATCH']:
            return await self.app(scope,receive,send)
        limit = 26*1024*1024 if scope['path']=='/api/references' else 128*1024
        headers = dict(scope['headers'])
        try:
            declared = int(headers.get(b'content-length',b'0'))
        except ValueError:
            declared = limit+1
        async def too_big():
            await JSONResponse({'detail':'Request body is too large'},413)(scope,receive,send)
        if declared > limit: return await too_big()
        # A disk-spooled, bounded copy also caps chunked uploads before FastAPI parses them.
        with SpooledTemporaryFile(max_size=1024*1024) as stream:
            size = 0
            while True:
                message = await receive()
                if message['type']=='http.disconnect': return
                chunk = message.get('body',b''); size += len(chunk)
                if size > limit: return await too_big()
                stream.write(chunk)
                if not message.get('more_body',False): break
            stream.seek(0)
            finished = False
            async def replay():
                nonlocal finished
                if finished: return await receive()
                chunk = stream.read(65536)
                more = stream.tell() < size
                finished = not more
                return {'type':'http.request','body':chunk,'more_body':more}
            await self.app(scope,replay,send)
