#!/usr/bin/env python3
"""Explicit one-shot counterpart. Loopback by default; no listener until invoked."""
import argparse,json,socket,struct,time

def remaining(s,deadline):
    left=deadline-time.monotonic()
    if left<=0:raise TimeoutError("absolute deadline")
    s.settimeout(left)
def exact(s,n,deadline):
    parts=[]
    while n:
        remaining(s,deadline)
        p=s.recv(min(n,65536))
        if not p:raise ValueError('premature EOF')
        parts.append(p);n-=len(p)
    return b''.join(parts)
def fnv(data,h=2166136261):
    for x in data:h=((h^x)*16777619)&0xffffffff
    return h
def serve(conn):
    deadline=time.monotonic()+30
    magic,version,n,seed=struct.unpack('!IIII',exact(conn,16,deadline))
    if magic!=0x4130344e or version!=1 or not 1<=n<=16777216 or seed!=37:raise ValueError('protocol header refused')
    payload=exact(conn,n,deadline)
    if any(x!=((i*17+(i>>12)+seed)&255) for i,x in enumerate(payload)):raise ValueError('upload mismatch')
    remaining(conn,deadline)
    conn.sendall(struct.pack('!I',fnv(payload)))
    result=bytes(x^0xa5 for x in payload);remaining(conn,deadline);conn.sendall(result)
    if struct.unpack('!I',exact(conn,4,deadline))[0]!=fnv(result):raise ValueError('download ACK mismatch')
    remaining(conn,deadline)
    conn.sendall(struct.pack('!I',0x50415353));return dict(verified=True,bytes_each_direction=n)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=0);a=p.parse_args()
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((a.bind,a.port));s.listen(1);s.settimeout(60)
        print(json.dumps(dict(phase='listening',address=s.getsockname())),flush=True)
        conn,_=s.accept()
        with conn:print(json.dumps(serve(conn)),flush=True)
if __name__=='__main__':main()
