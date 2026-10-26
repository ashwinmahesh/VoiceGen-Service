"""Private framed JSON IPC with per-call inherited ownership/accelerator leases."""
import array
import json
import socket
import struct
import os

MAX_MESSAGE=8*1024*1024


def send(sock,value,descriptors=()):
    payload=json.dumps(value,allow_nan=False).encode()
    if len(payload)>MAX_MESSAGE:raise ValueError('Backend message is too large')
    packet=struct.pack('!I',len(payload))+payload
    control=[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
    count=sock.sendmsg([packet],control)
    if count<len(packet):sock.sendall(packet[count:])


def receive(sock):
    header,ancillary,flags,_=sock.recvmsg(4,socket.CMSG_SPACE(32*array.array('i').itemsize))
    descriptors=[]
    for level,kind,data in ancillary:
        if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
            values=array.array('i');values.frombytes(data[:len(data)-len(data)%values.itemsize]);descriptors.extend(values)
    try:
        if not header:raise EOFError('Backend connection closed')
        while len(header)<4:
            part=sock.recv(4-len(header))
            if not part:raise EOFError('Backend connection closed')
            header+=part
        size=struct.unpack('!I',header)[0]
        if size>MAX_MESSAGE or flags&socket.MSG_CTRUNC:raise ValueError('Invalid backend message')
        chunks=[];remaining=size
        while remaining:
            part=sock.recv(min(65536,remaining))
            if not part:raise EOFError('Backend connection closed')
            chunks.append(part);remaining-=len(part)
        return json.loads(b''.join(chunks)),descriptors
    except BaseException:
        for fd in descriptors:os.close(fd)
        raise
