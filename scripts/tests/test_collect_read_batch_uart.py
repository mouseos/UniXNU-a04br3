#!/usr/bin/env python3
import importlib.util,json,os,tempfile,unittest
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

 def test_unterminated_corrupt_and_oversized_reasons_are_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'u';f=p/'f'
   raw=m.ANCHOR+b'\nBOOT_REASON=4_9\nBOOT_REASON='+b'9'*5000+b'\nBOOT_REASON=4'
   u.write_bytes(raw);f.write_text(json.dumps({'uart_offset':0,'boot_sha256':'a'*64}))
   r=m.collect(u,f,p/'out');self.assertEqual(r['boots'][0]['boot_reason_markers'],3);self.assertEqual(r['boots'][0]['boot_reasons'],[]);self.assertEqual((p/'out/post-flash.raw').read_bytes(),raw)
 def test_preloader_lines_not_android_command_line(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'u';f=p/'f'
   raw=(m.ANCHOR+b'\r\r\nBOOT_REASON: 4\r\r\n[18] BOOT_REASON: 4\r\r\n'
        b'[16708] cmdline: quiet boot_reason=4 androidboot.bootreason=wdt\r\r\n'+m.ANCHOR+b'\n[18] BOOT_REASON=9\n')
   u.write_bytes(raw);f.write_text(json.dumps({'uart_offset':0,'boot_sha256':'a'*64}))
   r=m.collect(u,f,p/'out');self.assertEqual(r['boots'][0]['boot_reason_markers'],2)
   self.assertEqual([x['value'] for x in r['boots'][0]['boot_reasons']],[4,4]);self.assertEqual([x['value'] for x in r['boots'][1]['boot_reasons']],[9])
   self.assertTrue(r['boots'][0]['closed_by_next_preloader']);self.assertEqual((p/'out/post-flash.raw').read_bytes(),raw)
 def test_malformed_standalone_reason_is_not_silenced(self):
  data=b'[18] BOOT_REASON: broken\ncmdline: boot_reason=4 other\nBOOT_REASON=4\n'
  self.assertEqual(len(list(m.REASON_MARK.finditer(data))),2);self.assertEqual([x['value'] for x in m.reason_values(data,0)],[4])
 def test_fifo_rejected_without_opening_and_duplicate_keys(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'fifo';os.mkfifo(u)
   with self.assertRaisesRegex(ValueError,'regular file'):m.checkpoint(u,p/'cp')
   with self.assertRaisesRegex(ValueError,'duplicate JSON'):m.strict_json(b'{"a":1,"a":2}')
 def test_partial_anchor_and_bounded_events(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'u';u.write_bytes(m.ANCHOR[:10]);cp=p/'cp';m.checkpoint(u,cp)
   u.write_bytes(m.ANCHOR+b'\n'+m.ANCHOR+b'\nBOOT_REASON=4\n')
   f=p/'f';f.write_text(json.dumps({'uart_offset':10,'boot_sha256':'a'*64,'uart_checkpoint':str(cp),'flash_completed_uart_offset':10}))
   r=m.collect(u,f,p/'out');self.assertTrue(r['offset_crosses_preloader_anchor'])
   events=m.event_lines(b'panic('*5000,m.PANIC,0);self.assertEqual(len(events),m.EVENT_LIMIT+1);self.assertEqual(events[-1],{'truncated':True})
 def test_event_budget_is_shared_across_boots(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'u';raw=m.ANCHOR+b'\nBOOT_REASON=4\npanic: one\n'+m.ANCHOR+b'\nBOOT_REASON=9\n';u.write_bytes(raw)
   f=p/'f';f.write_text(json.dumps({'uart_offset':0,'boot_sha256':'a'*64}));old=m.EVENT_LIMIT
   try:
    m.EVENT_LIMIT=2;r=m.collect(u,f,p/'out')
   finally:m.EVENT_LIMIT=old
   self.assertEqual(r['boots'][1]['boot_reasons'],[{'truncated':True}]);self.assertEqual((p/'out/post-flash.raw').read_bytes(),raw)
 def test_boot_limit_retains_raw(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);u=p/'u';raw=(m.ANCHOR+b'\n')*3;u.write_bytes(raw);f=p/'f';f.write_text(json.dumps({'uart_offset':0,'boot_sha256':'a'*64}));old=m.BOOT_LIMIT
   try:
    m.BOOT_LIMIT=2;r=m.collect(u,f,p/'out')
   finally:m.BOOT_LIMIT=old
   self.assertTrue(r['boots_truncated']);self.assertEqual(len(r['boots']),2);self.assertEqual((p/'out/post-flash.raw').read_bytes(),raw)

if __name__=='__main__':unittest.main()
