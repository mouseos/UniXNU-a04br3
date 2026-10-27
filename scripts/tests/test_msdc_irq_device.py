#!/usr/bin/env python3
"""Check generated board metadata and run the read-only benchmark on a fixture."""
from pathlib import Path
import struct, subprocess, tempfile
root=Path(__file__).resolve().parents[2]
def parse(blob,pos=0):
    n,c=struct.unpack_from('<II',blob,pos);pos+=8;props={}
    for _ in range(n):
        name,size=struct.unpack_from('<32sI',blob,pos);pos+=36
        props[name.split(b'\0')[0].decode()]=blob[pos:pos+size];pos+=(size+3)&~3
    children=[]
    for _ in range(c):
        child,pos=parse(blob,pos);children.append(child)
    return (props,children),pos
def find(node,name):
    props,children=node
    if props['name'].rstrip(b'\0').decode()==name:return props
    for child in children:
        result=find(child,name)
        if result:return result
    return None
with tempfile.TemporaryDirectory(prefix='msdc-device-') as d:
    d=Path(d);adt=d/'adt'
    subprocess.run(['python3',str(root/'scripts/make-a04br3-adt.py'),'--dram-size','0x80000000','--output',str(adt)],check=True)
    tree,end=parse(adt.read_bytes());assert end==adt.stat().st_size
    mmc=find(tree,'mmc0');assert struct.unpack('<I',mmc['msdc-irq-spi'])==(78,)
    assert struct.unpack('<Q',mmc['msdc-sysirq-base'])==(0x10200620,)
    assert struct.unpack('<I',mmc['msdc-irq-low'])==(1,)
    assert struct.unpack('<2Q',mmc['reg'])==(0x1120000,0x1000)
    exe=d/'bench';fixture=d/'image';fixture.write_bytes(bytes(range(256))*1024)
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(root/'tools/msdc-read-bench.c'),'-o',str(exe)],check=True)
    proc=subprocess.run([str(exe),str(fixture),'4096','65536','4'],capture_output=True,text=True,check=True)
    assert 'bytes=262144 iterations=4' in proc.stdout and 'process_cpu_ns=' in proc.stdout
    assert subprocess.run([str(exe),str(fixture),'0','0','1'],capture_output=True).returncode==2
    assert subprocess.run([str(exe),str(fixture),'0','1048576','1'],capture_output=True).returncode==1
    assert fixture.read_bytes()==bytes(range(256))*1024
print('PASS generated ADT: MSDC SPI 78, SYSIRQ base/low, unchanged reg; read-only userland bench and failures')
