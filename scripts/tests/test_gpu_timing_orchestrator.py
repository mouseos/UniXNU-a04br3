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
from unittest import mock
import os
import sys
import contextlib
import io

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('timing',ROOT/'scripts/run-a04br3-gpu-timing.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def boot(clock=26):
    return ('''A04BR3 SEC: RTC fastboot verified
Darwin Kernel Version 23.6
a04br3-autoreset: fastboot flag set
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
    def check_collector(self):pass
    def fastboot(self):return self.present
    def reset(self,folder):
        self.resets+=1;self.present=True;self.exit_code=0
        with self.a.uart_log.open('ab') as f:f.write(b'\nBOOT_REASON: 2\n')
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

    def test_live_collector_writer_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'collector'
            with path.open('a'):
                t=m.Transport(SimpleNamespace(collector_pid=os.getpid(),uart_log=path))
                t.check_collector()
            with self.assertRaises(RuntimeError):t.check_collector()

    def test_transport_and_offline_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);log=r/'collector';log.write_bytes(b'old')
            a=SimpleNamespace(serial='only-this-device',uart='/dev/fixture',uart_log=log)
            t=m.Transport(a)
            with mock.patch.dict(os.environ,{'UART':'bad-reader','USERDATA':'never-flash','NEXT_IMAGE_CMD':'never-run'}), \
                 mock.patch.object(m.subprocess,'Popen') as popen:
                t.start(r/'26.img',r,123)
                argv=popen.call_args.args[0];options=popen.call_args.kwargs
                self.assertEqual(argv[1],str(m.ROOT/'scripts/run-a04br3-unattended.sh'))
                self.assertEqual(options['env']['MODE'],'flash');self.assertEqual(options['env']['ROUNDS'],'1')
                self.assertFalse({'UART','USERDATA','NEXT_IMAGE_CMD'} & options['env'].keys())
                self.assertTrue(options['start_new_session'])
            argv=['fixture','--boot26','x','--boot380','y','--payload26','a','--payload380','b',
                  '--uart-log',str(log),'--out',str(r/'plan'),'--serial','only-this-device']
            with mock.patch.object(sys,'argv',argv),mock.patch.object(m,'validate_inputs',return_value={}), \
                 mock.patch.object(m,'Transport',side_effect=RuntimeError('must not execute')),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),0)
            self.assertFalse(json.loads((r/'plan/plan.json').read_text())['execute'])

    def test_parser(self):
        for c in (26,380):self.assertEqual(m.analyze(boot(c),c)['state'],'pass')
        for token in ('CP5 e200 r1','CE p5 q1 b2 t3','CX x169 e2'):
            self.assertEqual(m.analyze(boot().replace((token+'\n').encode(),b''),26)['state'],'pending')
        failures=[
                  boot().replace(b'A04BR3 SEC:',b'A04BR3 SEC: previous\nA04BR3 SEC:',1),
                  boot().replace(b'Darwin Kernel Version',b'BOOT_REASON: 2\nDarwin Kernel Version',1),
                  boot().replace(b'Darwin Kernel Version',b'Preloader Start\nDarwin Kernel Version',1),
                  b'Synchronous Exception at 0x123\n'+boot(),b'ASSERT [ArmCpuDxe] failure\n'+boot(),
                  boot()+b'powervr-metal[backboardd]: PF 2/1 r3 v1/0 t2 a2\n',
                  boot().replace(b'PF 1/0 r0 v1/0',b'PF 1/0 r0 v0/0'),
                  boot()+b'Darwin Kernel Version next\n',boot()+b'A04BR3 SEC: next\n',boot()+b'panic(cpu0)\n',boot()+b'panic(cpu0)',
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

    def run_case(self,mutation,transport=Fake):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);r=Path(directory.name)
        log=r/'collector.log';log.write_bytes(boot()) # prior successful boot MUST be outside epoch
        for c in (26,380):(r/str(c)).write_bytes(str(c).encode())
        a=SimpleNamespace(out=r/'run',uart_log=log,uart='unused',boot26=r/'26',boot380=r/'380',measurement_timeout=10,
                          recovery_timeout=5,settle=1,post_success=1,environment_json=None)
        a.out.mkdir();inputs={str(c):{'boot_sha256':m.sha(r/str(c))} for c in (26,380)};io=transport(a,mutation)
        status=m.campaign(a,inputs,io);result=json.loads((a.out/'campaign.json').read_text());return status,result,io,a

    def test_six_boots(self):
        status,result,io,a=self.run_case(lambda b:b)
        self.assertEqual(status,0);self.assertTrue(result['success']);self.assertEqual(io.sequence,list(m.ORDER));self.assertEqual(io.resets,6)
        self.assertEqual(result['summary']['26']['UI_encode_ms']['n'],3)
        previous=len(boot())
        for item in result['rounds']:
            self.assertEqual(item['epoch']['start_offset'],previous);self.assertGreater(item['epoch']['end_offset'],previous)
            previous=item['epoch']['recovery_end_offset'];self.assertEqual(item['state'],'pass')
            self.assertEqual(item['recovery_boot_reasons'],['2'])

    def test_missing_measurement_never_passes_despite_recovery(self):
        status,result,io,a=self.run_case(lambda b:b.replace(b'CX x169 e2\n',b''))
        self.assertEqual(status,2);self.assertFalse(result['success']);self.assertEqual(len(result['rounds']),1)
        self.assertIn('timeout',result['rounds'][0]['error']);self.assertEqual(io.resets,1)

    def test_uart_rotation_stops_schedule(self):
        class Rotating(Fake):
            def start(self,image,folder,limit):
                self.a.uart_log.rename(self.a.uart_log.with_suffix('.old'))
                self.a.uart_log.write_bytes(b'')
                return super().start(image,folder,limit)
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);r=Path(directory.name)
        log=r/'collector';log.write_bytes(b'old')
        image=r/'26';image.write_bytes(b'image');out=r/'out';out.mkdir()
        a=SimpleNamespace(out=out,uart_log=log,boot26=image,boot380=image,measurement_timeout=10,
                          recovery_timeout=5,settle=0,post_success=1,environment_json=None)
        inputs={'26':{'boot_sha256':m.sha(image)}}
        self.assertEqual(m.campaign(a,inputs,Rotating(a)),2)
        result=json.loads((out/'campaign.json').read_text())
        self.assertIn('rotated',result['rounds'][0]['error'])

    def test_transient_listing_and_transport_error(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);root=Path(directory.name)
        log=root/'collector';log.write_bytes(boot())
        folder=root/'round';folder.mkdir()
        m.save(folder/'result.json',{'epoch':{'identity':[log.stat().st_dev,log.stat().st_ino],'start_offset':0}})
        a=SimpleNamespace(uart_log=log,recovery_timeout=10)
        class Transient(Fake):
            def __init__(self,a):super().__init__(a);self.checks=0
            def fastboot(self):
                self.checks+=1
                return self.checks==1 or self.present
        io=Transient(a);io.present=False;io.exit_code=None
        m.recover(io,a,folder,Process(io));self.assertEqual(io.resets,1)
        io.exit_code=2
        with self.assertRaises(RuntimeError):m.recover(io,a,folder,Process(io))
        io.present=False;io.exit_code=None
        with self.assertRaises(TimeoutError):m.recover(io,a,folder,Process(io))
        self.assertEqual(io.resets,1)

    def test_dead_collector_preflight_has_no_device_effect(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);r=Path(directory.name)
        log=r/'collector';log.write_bytes(boot());out=r/'out';out.mkdir()
        a=SimpleNamespace(out=out,uart_log=log,recovery_timeout=5)
        class Dead(Fake):
            def check_collector(self):raise RuntimeError('dead collector')
        io=Dead(a);io.present=False
        self.assertEqual(m.campaign(a,{},io),2)
        self.assertEqual(io.resets,0);self.assertFalse(io.sequence)

    def test_fault_in_recovery_never_passes(self):
        class LateFault(Fake):
            def reset(self,folder):
                super().reset(folder)
                with self.a.uart_log.open('ab') as f:f.write(b'powervr: faults 0 hwr 1\n')
        status,result,io,a=self.run_case(lambda b:b,LateFault)
        self.assertEqual(status,2);self.assertFalse(result['success']);self.assertEqual(len(io.sequence),1)
        self.assertIn('recovery UART',result['rounds'][0]['recovery_error'])

    def test_panic_stops_schedule(self):
        status,result,io,a=self.run_case(lambda b:b+b'panic(cpu0)\n')
        self.assertEqual(status,2);self.assertEqual(len(io.sequence),1);self.assertEqual(result['rounds'][0]['state'],'fail')

if __name__=='__main__':unittest.main()
