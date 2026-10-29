#!/usr/bin/env python3
import hashlib,importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('observer',ROOT/'scripts/observe-read-batch-iostat.py');O=importlib.util.module_from_spec(spec);spec.loader.exec_module(O);C=O.C

def digest(b):return hashlib.sha256(b).hexdigest()
def sample(seq, t, n):
    lines=[f'a04br3 msdc io sample seq={seq} begin_ns={t} end_ns={t+2} non_atomic=1']
    for mode in ('dma_read','pio_read','write'):
        for size in ('le4k','4k_16k','gt16k'):
            lines.append(f'a04br3 msdc transfer seq={seq} mode={mode} size={size} calls={n} ok_bytes={n*512} errors=0 elapsed_ns={n*10}')
    for mode in ('read','write'):
        for size in ('le4k','4k_16k','gt16k'):
            lines.append(f'a04br3 msdc offered seq={seq} mode={mode} size={size} calls={n} bytes={n*1024}')
    lines.append(f'a04br3 msdc queue seq={seq} started={n} wait_ns={n*20} max_wait_ns={n*5}')
    lines.append(f'a04br3 msdc io end seq={seq} rows=16')
    return ('\n'.join(lines)+'\n').encode()

def boot(args,reason=4,panic=b''):
    return (C.ANCHOR+b'\n[BLDR] Build Time: fixture\nBOOT_REASON: '+str(reason).encode()+b'\nboot-args = '+args.encode()+b'\n'+sample(1,100,1)+sample(2,1000000100,3)+panic)

class Observer(unittest.TestCase):
 def fixture(self,root):
    uart=root/'uart';uart.write_bytes(b'old\n');args='x native-msdc-iostat=1'
    manifest={'measurement':'iostat','source_commit':'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f','device_commit':'8fde8ba87486338342f3be528823cb46394f2c5b','uefi_commit':'d3d4f28a89b29553ede7d7523c6af86d7e2459e8','variants':{}}
    runs=[]
    for v in ('baseline','batch'):
        image=root/(v+'.img');image.write_bytes(v.encode());a=args+(' native-cs-read-batch=1' if v=='batch' else '')
        manifest['variants'][v]={'kernel_args':a,'boot_sha256':digest(image.read_bytes())}
        runs.append({'id':v,'variant':v,'image':str(image),'flash_record':str(root/(v+'.json')),'conditions':{'cache':'unknown'}})
    mf=root/'pair.json';mf.write_text(json.dumps(manifest));plan={'schema':1,'uart':str(uart),'manifest':str(mf),'manifest_sha256':digest(mf.read_bytes()),'runs':runs}
    path=root/'plan.json';path.write_text(json.dumps(plan));return plan,manifest,path,uart
 def put(self,root,plan,manifest,uart,variant,**opts):
    entry=next(x for x in plan['runs'] if x['variant']==variant);cp=root/(variant+'.cp');C.checkpoint(uart,cp);offset=uart.stat().st_size
    flash={'uart_offset':offset,'flash_completed_uart_offset':offset,'uart_checkpoint':str(cp),'boot_sha256':manifest['variants'][variant]['boot_sha256'],'flash_success':True,'flash_success_evidence':'test fixture'}
    Path(entry['flash_record']).write_text(json.dumps(flash))
    with uart.open('ab') as f:f.write(boot(manifest['variants'][variant]['kernel_args'],**opts)+C.ANCHOR+b'\n[BLDR] Build Time: fixture\nBOOT_REASON: 4\n')
 def execute(self,plan,out):
    return subprocess.run(['python3',str(ROOT/'scripts/observe-read-batch-iostat.py'),'--plan',str(plan),'--out',str(out)],capture_output=True,text=True)
 def test_wait_resume_and_two_runs(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';r=self.execute(p,out);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads((out/'state.json').read_text())['runs'][0]['phase'],'awaiting_flash_record')
        self.put(root,plan,m,uart,'baseline');r=self.execute(p,out);self.assertEqual(r.returncode,0,r.stderr)
        state=json.loads((out/'state.json').read_text());self.assertEqual(state['runs'][0]['phase'],'finished');self.assertEqual(state['runs'][1]['phase'],'awaiting_flash_record')
        self.put(root,plan,m,uart,'batch');r=self.execute(p,out);self.assertEqual(r.returncode,0,r.stderr)
        report=json.loads((out/'comparison.json').read_text());self.assertEqual(report['comparison'],'exploratory_only_no_automatic_winner')
        for run in report['runs']:
            self.assertEqual(run['result']['state'],'recorded_conditions_unverified');self.assertEqual(run['result']['iostat']['intervals'],1);self.assertEqual(run['result']['iostat']['successful_read_bytes'],6144)
        self.assertFalse(self.execute(p,out).returncode)
 def test_panic_and_immutable_flash(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';self.put(root,plan,m,uart,'baseline',panic=b'panic(cpu 0): fail\n');self.assertEqual(self.execute(p,out).returncode,0)
        state=json.loads((out/'state.json').read_text());self.assertIn('first_boot_panic_candidate',state['runs'][0]['result']['reasons'])
        flash=Path(plan['runs'][1]['flash_record']);cp=root/'batch.cp';C.checkpoint(uart,cp);f={'uart_offset':uart.stat().st_size,'flash_completed_uart_offset':uart.stat().st_size,'uart_checkpoint':str(cp),'boot_sha256':m['variants']['batch']['boot_sha256'],'flash_success':True,'flash_success_evidence':'fixture'};flash.write_text(json.dumps(f));self.assertEqual(self.execute(p,out).returncode,0)
        f['flash_success_evidence']='changed';flash.write_text(json.dumps(f));self.assertEqual(self.execute(p,out).returncode,0);state=json.loads((out/'state.json').read_text());self.assertEqual(state['runs'][1]['phase'],'failed');self.assertIn('changed',state['runs'][1]['detail'])
 def test_manifest_and_identity_refusal(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';(root/'baseline.img').write_bytes(b'wrong');r=self.execute(p,out);self.assertEqual(r.returncode,2);self.assertIn('digest mismatch',r.stderr);self.assertFalse(out.exists())
        (root/'baseline.img').write_bytes(b'baseline');r=self.execute(p,out);self.assertEqual(r.returncode,0);plan['runs'][0]['conditions']={'cache':'cold'};p.write_text(json.dumps(plan));r=self.execute(p,out);self.assertEqual(r.returncode,2);self.assertIn('changed',r.stderr)
 def test_open_snapshot_then_watchdog_is_separate(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';cp=root/'baseline.cp';C.checkpoint(uart,cp);offset=uart.stat().st_size
        Path(plan['runs'][0]['flash_record']).write_text(json.dumps({'uart_offset':offset,'flash_completed_uart_offset':offset,'uart_checkpoint':str(cp),'boot_sha256':m['variants']['baseline']['boot_sha256'],'flash_success':True,'flash_success_evidence':'fixture'}))
        with uart.open('ab') as f:f.write(boot(m['variants']['baseline']['kernel_args']))
        self.assertEqual(self.execute(p,out).returncode,0)
        state=json.loads((out/'state.json').read_text());self.assertEqual(state['runs'][0]['phase'],'first_boot_open_snapshot_saved')
        with uart.open('ab') as f:f.write(C.ANCHOR+b'\n[BLDR] Build Time: fixture\nBOOT_REASON: 9\n')
        self.assertEqual(self.execute(p,out).returncode,0)
        result=json.loads((out/'baseline/result-final.json').read_text());self.assertIn('following_watchdog_reason_observed',result['reasons']);self.assertFalse(result['first_boot']['panics'])
 def test_interrupted_capture_is_not_overwritten(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';self.put(root,plan,m,uart,'baseline')
        (out/'baseline/capture-open').mkdir(parents=True);(out/'baseline/capture-open/evidence').write_text('retain')
        self.assertEqual(self.execute(p,out).returncode,0);state=json.loads((out/'state.json').read_text())
        self.assertEqual(state['runs'][0]['phase'],'failed');self.assertIn('interrupted capture',state['runs'][0]['detail']);self.assertEqual((out/'baseline/capture-open/evidence').read_text(),'retain')
 def test_equals_boot_reason_is_summarized(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';self.put(root,plan,m,uart,'baseline')
        uart.write_bytes(uart.read_bytes().replace(b'BOOT_REASON: 4',b'BOOT_REASON=4'))
        self.assertEqual(self.execute(p,out).returncode,0)
        result=json.loads((out/'baseline/result-final.json').read_text())
        self.assertEqual(result['state'],'recorded_conditions_unverified');self.assertEqual(result['iostat']['successful_read_bytes'],6144)
 def test_lk_command_line_does_not_create_reason_or_hide_bad_reason(self):
    for bad in (False,True):
     with self.subTest(bad=bad),tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';self.put(root,plan,m,uart,'baseline')
        extra=b'[18] BOOT_REASON: '+(b'bad' if bad else b'4')+b'\n[16708] cmdline: x boot_reason=4 androidboot.bootreason=wdt\n'
        uart.write_bytes(uart.read_bytes().replace(b'BOOT_REASON: 4\n',b'BOOT_REASON: 4\n'+extra,1))
        self.assertEqual(self.execute(p,out).returncode,0);result=json.loads((out/'baseline/result-final.json').read_text())
        if bad:self.assertIn('first_boot_reason_missing_or_unparsed',result['reasons'])
        else:self.assertEqual(result['state'],'recorded_conditions_unverified');self.assertEqual(result['first_boot']['boot_reason_markers'],2)
 def test_false_anchor_and_fake_reason_do_not_close_boot(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';cp=root/'baseline.cp';C.checkpoint(uart,cp);offset=uart.stat().st_size
        Path(plan['runs'][0]['flash_record']).write_text(json.dumps({'uart_offset':offset,'flash_completed_uart_offset':offset,'uart_checkpoint':str(cp),'boot_sha256':m['variants']['baseline']['boot_sha256'],'flash_success':True,'flash_success_evidence':'fixture'}))
        with uart.open('ab') as f:f.write(boot(m['variants']['baseline']['kernel_args'])+b'log: '+C.ANCHOR+b'\ncmdline: boot_reason=4\n')
        self.assertEqual(self.execute(p,out).returncode,0);s=json.loads((out/'state.json').read_text());self.assertEqual(s['runs'][0]['anchors'],1)
        with uart.open('ab') as f:f.write(C.ANCHOR+b'\ncmdline: boot_reason=4\n')
        self.assertEqual(self.execute(p,out).returncode,0);s=json.loads((out/'state.json').read_text());self.assertEqual(s['runs'][0]['anchors'],2);self.assertFalse(s['runs'][0]['second_reason'])
        with uart.open('ab') as f:f.write(b'BOOT_REASON: 9\n')
        self.assertEqual(self.execute(p,out).returncode,0);s=json.loads((out/'state.json').read_text());self.assertNotEqual(s['runs'][0]['phase'],'finished')
        with uart.open('ab') as f:f.write(b'[BLDR] Build Time: fixture\n')
        self.assertEqual(self.execute(p,out).returncode,0);s=json.loads((out/'state.json').read_text());self.assertEqual(s['runs'][0]['phase'],'finished');self.assertIn('following_watchdog_reason_observed',s['runs'][0]['result']['reasons'])
 def test_next_boot_evidence_cannot_come_from_third_boot(self):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';cp=root/'baseline.cp';C.checkpoint(uart,cp);offset=uart.stat().st_size
        Path(plan['runs'][0]['flash_record']).write_text(json.dumps({'uart_offset':offset,'flash_completed_uart_offset':offset,'uart_checkpoint':str(cp),'boot_sha256':m['variants']['baseline']['boot_sha256'],'flash_success':True,'flash_success_evidence':'fixture'}))
        with uart.open('ab') as f:f.write(boot(m['variants']['baseline']['kernel_args'])+C.ANCHOR+b'\nBOOT_REASON: 4\n'+C.ANCHOR+b'\n[BLDR] Build Time: fixture\nBOOT_REASON: 9\n')
        self.assertEqual(self.execute(p,out).returncode,0);result=json.loads((out/'baseline/result-final.json').read_text())
        self.assertEqual(result['state'],'incomplete_or_excluded');self.assertIn('following_boot_bldr_missing',result['reasons']);self.assertNotIn('following_watchdog_reason_observed',result['reasons'])
 def test_resume_rejects_corrupt_saved_raw_evidence(self):
    for filename in ('post-flash.raw','boot-1.raw','flash-record.json','uart-checkpoint.json'):
     with self.subTest(filename=filename),tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';self.put(root,plan,m,uart,'baseline')
        self.assertEqual(self.execute(p,out).returncode,0)
        state=json.loads((out/'state.json').read_text());run=state['runs'][0]
        result=O.capture(out,state,run,plan['runs'][0],m,plan,'final');self.assertEqual(result['state'],'recorded_conditions_unverified')
        (out/'baseline/capture-final'/filename).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):O.capture(out,state,run,plan['runs'][0],m,plan,'final')
 def test_finished_run_reverified_before_comparison_and_on_resume(self):
    for when in ('before_comparison','after_comparison'):
     with self.subTest(when=when),tempfile.TemporaryDirectory() as d:
        root=Path(d);plan,m,p,uart=self.fixture(root);out=root/'out';self.put(root,plan,m,uart,'baseline')
        self.assertEqual(self.execute(p,out).returncode,0)
        if when=='after_comparison':
            self.put(root,plan,m,uart,'batch');self.assertEqual(self.execute(p,out).returncode,0)
        (out/'baseline/capture-final/boot-1.raw').write_bytes(b'corrupt')
        if when=='before_comparison':self.put(root,plan,m,uart,'batch')
        self.assertEqual(self.execute(p,out).returncode,0)
        report=json.loads((out/'comparison.json').read_text());baseline=report['runs'][0]
        self.assertEqual(baseline['phase'],'failed');self.assertIsNone(baseline['result']);self.assertIn('saved evidence',baseline['detail'])

if __name__=='__main__':unittest.main()
