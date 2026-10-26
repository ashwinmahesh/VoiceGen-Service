"""One managed backend process. No HTTP listener, queue or independent worker lease."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import socket
from backend_protocol import send,receive


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
        send(channel,{'ready':True})
        while True:
            try:request,fds=receive(channel)
            except EOFError:break
            with ExitStack() as stack:
                handles=[stack.enter_context(os.fdopen(fd,'rb',closefd=True)) for fd in fds]
                try:
                    with inherit_reservations(*handles):
                        if request['command']=='close':
                            engine.unload();send(channel,{'ok':True});return
                        if request['command']!='generate':raise ValueError('Unknown backend command')
                        result=engine.generate(Generation.model_validate(request['request']),
                            Path(request['reference']) if request['reference'] else None,Path(request['destination']),
                            lambda stage:send(channel,{'event':stage}),transcript=request['transcript'])
                        send(channel,{'result':result,'loaded':engine.model is not None,'variant':engine.variant})
                except Exception as error:
                    send(channel,{'error':str(error),'loaded':engine.model is not None,'variant':engine.variant})


if __name__=='__main__':main()
