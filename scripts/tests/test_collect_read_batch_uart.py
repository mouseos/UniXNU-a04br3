#!/usr/bin/env python3
import importlib.util,json,tempfile,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'collect-read-batch-uart.py'
s=importlib.util.spec_from_file_location('capture',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Capture(unittest.TestCase):
 def test_offsets_and_separate_panic_reboot(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);uart=p/'uart';old=b'previous boot\n';first=m.ANCHOR+b'\nBOOT_REASON: 4\npanic(cpu 1): fail\n';second=m.ANCHOR+b'\nBOOT_REASON=9\nfastboot\n'
   uart.write_bytes(old);cp=p/'checkpoint.json';m.checkpoint(uart,cp);uart.write_bytes(old+b'flash noise\n'+first+second);f=p/'flash.json';f.write_text(json.dumps({'uart_offset':len(old),'boot_sha256':'a'*64,'flash_success':True,'flash_success_evidence':'fastboot log','flash_completed_uart_offset':len(old),'uart_checkpoint':str(cp)}))
   r=m.collect(uart,f,p/'out');self.assertEqual(len(r['boots']),2);self.assertTrue(r['boots'][0]['closed_by_next_preloader']);self.assertTrue(r['first_anchor_after_flash']);self.assertEqual(r['boots'][0]['boot_reasons'][0]['value'],4);self.assertEqual(len(r['boots'][0]['panics']),1);self.assertEqual(r['boots'][1]['boot_reasons'][0]['value'],9);self.assertFalse(r['boots'][1]['panics']);self.assertEqual((p/'out/boot-1.raw').read_bytes(),first)
   with self.assertRaises(ValueError):m.collect(uart,f,p/'out')
 def test_bad_offset_no_anchor_and_limit(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'uart';u.write_bytes(b'abc');f=p/'f';o=p/'out'
   for offset in (-1,4):
    f.write_text(json.dumps({'uart_offset':offset,'boot_sha256':'a'*64}))
    with self.assertRaises(ValueError):m.collect(u,f,o)
   f.write_text(json.dumps({'uart_offset':0,'boot_sha256':'a'*64,'flash_completed_uart_offset':1}));r=m.collect(u,f,p/'no-anchor');self.assertEqual(r['boots'],[]);self.assertEqual((p/'no-anchor/post-flash.raw').read_bytes(),b'abc');self.assertEqual(r['prefix_segments'][1]['start_offset'],1)
   u.write_bytes(m.ANCHOR+b'\n');f.write_text(json.dumps({'uart_offset':0,'boot_sha256':'b'*64}))
   with self.assertRaises(ValueError):m.collect(u,f,o,2)
   self.assertFalse(o.exists())
 def test_checkpoint_rejects_replacement_and_changed_tail(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'uart';u.write_bytes(b'abcdef');cp=p/'cp';m.checkpoint(u,cp)
   f=p/'f';f.write_text(json.dumps({'uart_offset':6,'boot_sha256':'d'*64,'uart_checkpoint':str(cp)}))
   u.write_bytes(b'abcdeg'+m.ANCHOR+b'\n')
   with self.assertRaises(ValueError):m.collect(u,f,p/'bad')
   replacement=p/'replacement';replacement.write_bytes(b'abcdef'+m.ANCHOR+b'\n');replacement.replace(u)
   with self.assertRaises(ValueError):m.collect(u,f,p/'bad2')
   self.assertFalse((p/'bad').exists());self.assertFalse((p/'bad2').exists())
 def test_prefix_events_and_hex_reason(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'uart';u.write_bytes(b'old');cp=p/'cp';m.checkpoint(u,cp)
   prefix=b'kernel panic old\n';u.write_bytes(b'old'+prefix+m.ANCHOR+b'\nBOOT_REASON=0x9\n')
   f=p/'f';f.write_text(json.dumps({'uart_offset':3,'boot_sha256':'e'*64,'uart_checkpoint':str(cp),'flash_completed_uart_offset':3}))
   r=m.collect(u,f,p/'out');self.assertEqual(len(r['pre_preloader']['panics']),1)
   self.assertEqual(r['pre_preloader']['panics'][0]['absolute_offset'],3)
   self.assertEqual(r['boots'][0]['boot_reasons'][0]['value'],9)

 def test_open_and_unverified_flash(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'uart').write_bytes(m.ANCHOR+b'\n');(p/'f').write_text(json.dumps({'uart_offset':0,'boot_sha256':'c'*64}))
   r=m.collect(p/'uart',p/'f',p/'out');self.assertFalse(r['flash_success_claimed']);self.assertFalse(r['boots'][0]['closed_by_next_preloader']);self.assertEqual(r['boots'][0]['panics'],[])

if __name__=='__main__':unittest.main()
