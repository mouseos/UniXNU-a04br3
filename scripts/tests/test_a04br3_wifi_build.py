#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Test actual firmware acquisition/packing and opt-in build selection."""
from pathlib import Path
import hashlib,sys,tempfile,unittest,tomllib,json,os
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import a04br3_wifi_build as build

class Firmware(unittest.TestCase):
 def test_exact_inputs_cached_corruption_refused_and_patch_set_validated(self):
  inputs={'WIFI_RAM_CODE_MT6630':b'mock WLAN input'}
  for sequence in (1,2):
   raw=bytearray(64);raw[:16]=b'2026010100000000';raw[16:20]=b'TEST'
   raw[20:24]=bytes.fromhex('8a128a11');raw[24]=0x20|sequence
   inputs[f'mt6630_patch_e3_{sequence-1}_hdr.bin']=bytes(raw)
  hashes={n:hashlib.sha256(b).hexdigest() for n,b in inputs.items()}
  class Response:
   def __init__(self,data):self.data=data
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,limit):return self.data[:limit]
  urls=[]
  def fetch(url,timeout):
   urls.append(url);self.assertEqual(timeout,120)
   self.assertIn('/'+build.VENDOR_REF+'/',url)
   return Response(inputs[url.rsplit('/',1)[-1]])
  with tempfile.TemporaryDirectory() as tmp,patch.object(build,'FIRMWARE',hashes),patch.object(build.urllib.request,'urlopen',fetch):
   out=Path(tmp);wmt,wlan=build.firmware(out)
   self.assertEqual(len(urls),3);self.assertEqual(wlan.read_bytes(),inputs[wlan.name]);self.assertTrue(wmt.read_bytes().startswith(b'A04WMT1\0'))
   build.firmware(out);self.assertEqual(len(urls),3)
   wlan.write_bytes(b'changed');self.assertRaisesRegex(ValueError,'cached firmware hash mismatch',build.firmware,out)
   self.assertEqual(len(urls),3)
 def test_board_archive_opt_in_is_exact_and_source_list_has_single_network_owner(self):
  sources=build.source_files(ROOT/'package/wifi')
  self.assertEqual(sum(p.name=='A04BR3SDIO.cpp' for p in sources),1)
  self.assertEqual(sum(p.name=='A04BR3Ethernet.cpp' for p in sources),1)
  self.assertEqual(len(sources),len(set(sources)))
  source=(ROOT/'scripts/build-a04br3-board.py').read_text()
  self.assertIn("if os.environ.get('A04BR3_WIFI') == '1':",source)
  self.assertIn('objects += build_wifi(',source)
  native=(ROOT/'scripts/build-arm-driver.py').read_text()
  self.assertIn("if os.environ.get('A04BR3_WIFI') == '1':\n        command += ['-DNATIVE_MSDC_PARTITION_READ=1']",native)
  board=(ROOT/'package/A04BR3Board.cpp').read_text()
  self.assertLess(board.index('extern "C" int unixnu_board_launch'),board.index('  a04br3_wifi_init();'))
  self.assertIn('#if A04BR3_WIFI',board)
  roots=tomllib.loads((ROOT/'board.toml').read_text())['kernel']['prelink']['roots']
  self.assertNotIn('com.apple.iokit.IONetworkingFamily',roots)
  self.assertNotIn('com.apple.iokit.IO80211Family',roots)

 def test_actual_builder_passes_private_spi_to_every_cpp_and_no_other_sources(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp);wmt=out/'wmt.bin';wlan=out/'wlan.bin';wmt.write_bytes(b'wmt fixture');wlan.write_bytes(b'wlan fixture')
   calls=[];guarded=[]
   def guard(*args):guarded.append(True)
   def compile_one(record,source,flags):
    self.assertTrue(guarded);calls.append((record,source,list(flags)))
    if source.suffix=='.cpp':
     self.assertEqual(flags.count('-D__PRIVATE_SPI__=1'),1)
     self.assertIn('-DA04BR3_WIFI=1',flags)
    else:self.assertNotIn('-D__PRIVATE_SPI__=1',flags)
    return out/(source.stem+'.o')
   with patch.object(build,'guard_prelink',guard),patch.object(build,'firmware',return_value=(wmt,wlan)),patch.object(build.subprocess,'run'):
    result=build.build_wifi(ROOT,ROOT/'package',out,compile_one,'cxx-record','c-record',[],[],None,None)
   cpp=[p for rec,p,flags in calls if rec=='cxx-record']
   self.assertEqual(cpp,build.source_files(ROOT/'package/wifi'))
   self.assertEqual(len(guarded),1);self.assertEqual(len(result),len(calls)+1)
   self.assertEqual(json.loads((out/'wifi-build.json').read_text())['runtime_opt_in'],'a04br3-wifi=1')

 def test_fixture_license_hash_and_source_scope_are_recorded(self):
  fixture=ROOT/'package/wifi/host-fixture';meta=json.loads((fixture/'SOURCE.json').read_text())['port_test_sources']
  self.assertEqual(meta['commit'],'f5d43bf1677955e7e367545b9bbbd6449776cdda')
  self.assertEqual(len(meta['files']),3)
  self.assertEqual(hashlib.sha256((fixture/meta['license']['vendored_path']).read_bytes()).hexdigest(),meta['license']['sha256'])
  for row in meta['files']:self.assertEqual(hashlib.sha256((fixture/row['path']).read_bytes()).hexdigest(),row['sha256'])

if __name__=='__main__':unittest.main()
