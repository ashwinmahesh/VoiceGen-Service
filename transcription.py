"""Bounded, short-lived CPU transcription of already validated reference WAVs."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from config import PROJECT_ROOT
from transcription_assets import SPEC,check_ready


class TranscriptionError(RuntimeError):
    def __init__(self,message,status=503):super().__init__(message);self.status=status


async def stop_process(process):
    if process.returncode is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:await asyncio.wait_for(process.wait(),2)
        except asyncio.TimeoutError:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait()


async def transcribe_reference(audio,root,language='auto',disconnected=None):
    if language!='auto' and language not in SPEC['languages']:raise TranscriptionError('Use auto or a supported Whisper language code, such as en.',422)
    try:binary,model=check_ready()
    except (OSError,RuntimeError,ValueError) as exc:raise TranscriptionError(str(exc)) from None
    state=PROJECT_ROOT/'state';state.mkdir(exist_ok=True)
    with (state/'transcription.lock').open('a+') as lease:
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise TranscriptionError('Another recording is being transcribed. Try again shortly or enter its transcript manually.',409) from None
        scratch=Path(root)/'cache/tmp';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='transcription-',dir=scratch) as temporary:
            output=Path(temporary)/'result';process=None;started=time.monotonic()
            try:
                with (Path(temporary)/'runtime.log').open('wb') as log:
                    process=await asyncio.create_subprocess_exec(str(binary),'--model',str(model),'--file',str(audio),
                        '--no-gpu','--threads',str(SPEC['threads']),'--processors','1','--language',language,
                        '--output-json','--output-file',str(output),'--no-prints',
                        stdout=asyncio.subprocess.DEVNULL,stderr=log,start_new_session=True,pass_fds=(lease.fileno(),))
                    while process.returncode is None:
                        if disconnected and await disconnected():raise TranscriptionError('Transcription cancelled because the upload connection closed.',499)
                        if time.monotonic()-started>SPEC['timeout_seconds']:raise TranscriptionError('Transcription timed out. Retry, or enter the exact words manually.',504)
                        try:await asyncio.wait_for(process.wait(),.2)
                        except asyncio.TimeoutError:pass
                result=output.with_suffix('.json')
                if process.returncode or not result.is_file() or result.stat().st_size>1024*1024:raise TranscriptionError('Local transcription failed. Retry or enter the exact words manually.')
                data=json.loads(result.read_text())
                segments=data['transcription']
                if not isinstance(segments,list) or any(not isinstance(segment,dict) or not isinstance(segment.get('text'),str) for segment in segments):raise ValueError('Invalid segments')
                text=' '.join(segment['text'].strip() for segment in segments if segment['text'].strip()).strip()
                if not text or text in ('[BLANK_AUDIO]','[Silence]','[Music]'):
                    raise TranscriptionError('No speech was recognized. Enter the words manually or try a clearer recording.',422)
                if len(text)>3000:raise TranscriptionError('Detected transcript is too long. Use a shorter recording or enter its exact words manually.',422)
                receipt={'engine':SPEC['engine'],'model':SPEC['model'],'language':data.get('result',{}).get('language',language),
                         'seconds':round(time.monotonic()-started,3),'device':'CPU'}
                return text,receipt
            except (OSError,ValueError,KeyError,TypeError) as exc:
                raise TranscriptionError('Local transcription could not read its result. Retry or enter the exact words manually.') from None
            finally:
                if process is not None:await stop_process(process)
