"""One managed backend process. No HTTP listener, queue or independent worker lease."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import threading
from backend_protocol import send,receive
from lifecycle_events import observe, reason


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fd',type=int,required=True);parser.add_argument('--owner-fd',type=int,required=True)
    args=parser.parse_args()
    from engine import create_engine
    from schemas import Generation
    from inference_locks import inherit_reservations
    engine=create_engine()
    # Deliberately leave this raw descriptor open until OS process exit, including
    # interpreter teardown after an EOF. File-object cleanup could release it
    # while model tensors or native descendants still retain memory.
    os.fstat(args.owner_fd)
    with socket.socket(fileno=args.fd) as channel:
        message_lock=threading.Lock()
        def report(value):
            with message_lock:send(channel,value)
        report({'ready':True})
        while True:
            try:request,fds=receive(channel)
            except EOFError:break
            with ExitStack() as stack:
                handles=[stack.enter_context(os.fdopen(fd,'rb',closefd=True)) for fd in fds]
                try:
                    with inherit_reservations(*handles),observe(lambda event:report({'lifecycle':event})):
                        if request['command']=='close':
                            with reason(request.get('reason')):engine.unload()
                            report({'ok':True});return
                        if request['command']!='generate':raise ValueError('Unknown backend command')
                        result=engine.generate(Generation.model_validate(request['request']),
                            Path(request['reference']) if request['reference'] else None,Path(request['destination']),
                            lambda stage:report({'event':stage}),transcript=request['transcript'])
                        report({'result':result,'loaded':engine.model is not None,'variant':engine.variant})
                except Exception as error:
                    report({'error':str(error),'loaded':engine.model is not None,'variant':engine.variant})


if __name__=='__main__':main()
