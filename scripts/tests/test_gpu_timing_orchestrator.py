#!/usr/bin/env python3
"""Production parser/campaign state machine with injected host-only transport."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import struct
import zlib
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('timing',ROOT/'scripts/run-a04br3-gpu-timing.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def boot(clock=26):
    return ('''A04BR3 SEC: RTC fastboot verified
Darwin Kernel Version 23.6
'''+f'a04br3 gpu clock: active parent {4 if clock==26 else 7} core {26000000 if clock==26 else 380000062} Hz\n'+'''powervrprobed: start: pid 2 uid 0
powervrprobed: vk buffer-rows: rows=1280 width=3152 srcPitch=3328 dstPitch=3200 prefix=4096
powervrprobed: vk buffer-rows allocation 0 requested=4268032 actual=4268032
powervrprobed: vk buffer-rows allocation 1 requested=4104192 actual=4104192
powervrprobed: PASS vk rows submit: VkResult 0
powervrprobed: PASS vk rows wait: VkResult 0
powervrprobed: PASS vk rows payload: checked=4034560 mismatches=0
powervrprobed: PASS vk rows all padding/prefix/allocation-tail: checked=69632 mismatches=0
powervrprobed: PASS vk rows source unchanged: checked=4268032 mismatches=0
powervrprobed: vk buffer-rows single-shot host-us: record=10 submit=20 wait=30 validate=40 clock=1
powervrprobed: vk buffer-rows single-shot cache-us: flush=1 invalidate=2 clock=1
powervrprobed: vulkan stage passed
powervrprobed: done: 77 passed, 0 failed, status 0
powervr: MIPS exception status 20 wrapper status 0; HWR counter 0; fault0 data0
powervr-metal[backboardd]: IM main=100000000 slide=0
powervr-metal[backboardd]: CG w800 h1280 a16 r2
powervr-metal[backboardd]: CS s3328 d3200
powervr-metal[backboardd]: CP5 e200 r1
powervr-metal[backboardd]: CT i0 n177 c16 w6
powervr-metal[backboardd]: CE p5 q1 b2 t3
powervr-metal[backboardd]: CX x169 e2
powervr-metal[backboardd]: PF 1/0 r0 v1/0 t200 a200
''').encode()

class Process:
    def __init__(self,io):self.io=io
    def poll(self):return self.io.exit_code

class Fake:
    def __init__(self,a,mutation=lambda b:b):self.a=a;self.time=0;self.present=True;self.exit_code=None;self.sequence=[];self.resets=0;self.mutation=mutation
    def now(self):return self.time
    def sleep(self,n):self.time+=n
    def fastboot(self):return self.present
    def reset(self,folder):self.resets+=1;self.present=True;self.exit_code=0
    def start(self,image,folder,limit):
        clock=int(image.stem);self.sequence.append(clock);self.present=False;self.exit_code=None
        with self.a.uart_log.open('ab') as f:f.write(self.mutation(boot(clock)))
        return Process(self)
    def stop(self,p):pass

class Tests(unittest.TestCase):
    def test_actual_boot_report_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);values={}
            for clock in (26,380):
                payload=('payload'+str(clock)).encode()
                z=zlib.compressobj(wbits=31);body=z.compress(b'firmware')+z.flush()+b'\xd0\x0d\xfe\xed'
                kh=bytearray(512);struct.pack_into('<II',kh,0,0x58881688,len(body));kh[8:14]=b'KERNEL'
                kernel=bytes(kh)+body;rh=bytearray(512);struct.pack_into('<II',rh,0,0x58881688,len(payload));rh[8:14]=b'ROOTFS'
                header=bytearray(2048);header[:8]=b'ANDROID!'
                struct.pack_into('<8I',header,8,len(kernel),0x40080000,512+len(payload),0x60000000,0,0,0x4e000000,2048)
                header[64:64+len(b'bootopt=64S3,32N2,64N2')]=b'bootopt=64S3,32N2,64N2'
                image=root/str(clock);image.write_bytes(header+kernel+bytes(-len(kernel)%2048)+rh+payload)
                import hashlib
                report={'payload_sha256':hashlib.sha256(payload).hexdigest(),
                        'kernel_args':'-v a04br3-powervrprobed=1 a04br3-gpu-clock='+('0' if clock==26 else '380')}
                report.update({k:'same' for k in ('kernel_sha256','payload_kernel_sha256','data_ramdisk_sha256','gpu_addon_sha256','trust_cache_sha256')})
                manifest=root/(str(clock)+'.json');manifest.write_text(json.dumps(report))
                values['boot'+str(clock)]=image;values['payload'+str(clock)]=manifest
            args=SimpleNamespace(**values);m.validate_inputs(args)
            data=json.loads(args.payload380.read_text());data['payload_sha256']='wrong';args.payload380.write_text(json.dumps(data))
            with self.assertRaises(ValueError):m.validate_inputs(args)

    def test_parser(self):
        for c in (26,380):self.assertEqual(m.analyze(boot(c),c)['state'],'pass')
        for token in ('CP5 e200 r1','CE p5 q1 b2 t3','CX x169 e2'):
            self.assertEqual(m.analyze(boot().replace((token+'\n').encode(),b''),26)['state'],'pending')
        failures=[boot()+b'Darwin Kernel Version next\n',boot()+b'A04BR3 SEC: next\n',boot()+b'panic(cpu0)\n',boot()+b'panic(cpu0)',
                  boot()+b'powervr: HWR counter 1\n',boot()+b'powervr: context reset 4\n',
                  boot().replace(b'77 passed, 0 failed',b'76 passed, 1 failed'),
                  boot().replace(b'checked=69632 mismatches=0',b'checked=69632 mismatches=1'),
                  boot().replace(b'wait=30 validate=40 clock=1',b'wait=30 validate=40 clock=0'),
                  boot().replace(b'CP5 e200 r1',b'CP5 e10 r1'),
                  boot().replace(b'start: pid 2',b'start: pid 2\npowervrprobed: start: pid 3'),
                  boot().replace(b'A04BR3 SEC: RTC fastboot verified\n',b'')]
        for raw in failures:self.assertEqual(m.analyze(raw,26)['state'],'fail',raw[-120:])
        self.assertEqual(m.analyze(boot(26),380)['state'],'fail')
        self.assertEqual(m.analyze(boot(380).replace(b'380000062',b'299000000'),380)['state'],'fail')
        self.assertEqual(m.analyze(boot().replace(b'\n',b'\\r\\n'),26)['state'],'pass')

    def run_case(self,mutation):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);r=Path(directory.name)
        log=r/'collector.log';log.write_bytes(boot()) # prior successful boot MUST be outside epoch
        for c in (26,380):(r/str(c)).write_bytes(str(c).encode())
        a=SimpleNamespace(out=r/'run',uart_log=log,uart='unused',boot26=r/'26',boot380=r/'380',measurement_timeout=10,
                          recovery_timeout=5,settle=1,post_success=1,environment_json=None)
        a.out.mkdir();inputs={str(c):{'boot_sha256':m.sha(r/str(c))} for c in (26,380)};io=Fake(a,mutation)
        status=m.campaign(a,inputs,io);result=json.loads((a.out/'campaign.json').read_text());return status,result,io,a

    def test_six_boots(self):
        status,result,io,a=self.run_case(lambda b:b)
        self.assertEqual(status,0);self.assertTrue(result['success']);self.assertEqual(io.sequence,list(m.ORDER));self.assertEqual(io.resets,6)
        self.assertEqual(result['summary']['26']['UI_encode_ms']['n'],3)
        previous=len(boot())
        for item in result['rounds']:
            self.assertEqual(item['epoch']['start_offset'],previous);self.assertGreater(item['epoch']['end_offset'],previous)
            previous=item['epoch']['end_offset'];self.assertEqual(item['state'],'pass')

    def test_missing_measurement_never_passes_despite_recovery(self):
        status,result,io,a=self.run_case(lambda b:b.replace(b'CX x169 e2\n',b''))
        self.assertEqual(status,2);self.assertFalse(result['success']);self.assertEqual(len(result['rounds']),1)
        self.assertIn('timeout',result['rounds'][0]['error']);self.assertEqual(io.resets,1)

    def test_panic_stops_schedule(self):
        status,result,io,a=self.run_case(lambda b:b+b'panic(cpu0)\n')
        self.assertEqual(status,2);self.assertEqual(len(io.sequence),1);self.assertEqual(result['rounds'][0]['state'],'fail')

if __name__=='__main__':unittest.main()
