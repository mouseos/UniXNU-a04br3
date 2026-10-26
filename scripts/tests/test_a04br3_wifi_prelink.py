#!/usr/bin/env python3
"""Real Mach-O/plist dependency guards, with explicit canonical NativePrelink source."""
from pathlib import Path
import argparse,hashlib,json,os,plistlib,struct,sys,tempfile,unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import a04br3_wifi_build as build

SOURCE=None

def image(entries):
 data=plistlib.dumps({'_PrelinkInfoDictionary':entries}).split(b'<plist version="1.0">',1)[1].rsplit(b'</plist>',1)[0].strip()
 header=struct.pack('<8I',0xfeedfacf,0x100000c,0,2,1,72,0,0)
 segment=struct.pack('<II16s4Q4I',0x19,72,b'__PRELINK_INFO',0x10000000,((len(data)+4095)//4096)*4096,4096,len(data),7,3,0,0)
 return (header+segment).ljust(4096,b'\0')+data

def entry(name,deps=(),resource=False):
 return {'CFBundleIdentifier':name,'OSBundleLibraries':dict.fromkeys(deps,'1'),'OSKernelResource':resource}

class Prelink(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  kit=self.root/'kits/NativePrelink';kit.mkdir(parents=True)
  (kit/'native_prelink.py').symlink_to(SOURCE.resolve())
  self.cache=self.root/'cache'
  self.profile={'tool':'kits/NativePrelink/emit_prelink.py','roots':['root']}
 def env(self,entries):
  self.cache.write_bytes(image(entries));self.profile['kernelcache_sha256']=hashlib.sha256(self.cache.read_bytes()).hexdigest()
  return {'A04BR3_WIFI':'1','UNIXNU_PRELINK_SPEC':json.dumps(self.profile),'UNIXNU_PRELINK_KERNELCACHE':str(self.cache)}
 def test_default_off_never_reads_inputs(self):
  for flag in (None,'','0','2','01','true'):
   self.assertIsNone(build.guard_prelink(self.root,{'A04BR3_WIFI':flag}))
 def test_real_metadata_unselected_families_allowed_cycle_and_interface(self):
  report=build.guard_prelink(self.root,self.env([entry('root',['child','kernel']),entry('child',['root']),entry('kernel',['ignored'],True),*[entry(x) for x in build.BLOCKED_NETWORK_FAMILIES]]))
  self.assertEqual(report['selected'],['child','root']);self.assertEqual(report['interfaces'],['kernel'])
 def test_direct_and_transitive_family_refused(self):
  for family in build.BLOCKED_NETWORK_FAMILIES:
   for roots in ([family],['root']):
    self.profile['roots']=roots
    with self.assertRaisesRegex(ValueError,'conflicts'):
     build.guard_prelink(self.root,self.env([entry('root',['child']),entry('child',[family]),entry(family)]))
 def test_extra_roots_are_effective_and_input_rewrites_fail_closed(self):
  for extra in (['--root','com.apple.iokit.IO80211Family'],['--root=com.apple.iokit.IONetworkingFamily']):
   self.profile['extra_args']=extra
   with self.assertRaisesRegex(ValueError,'conflicts'):build.guard_prelink(self.root,self.env([entry('root')]))
  for extra in (['--prelinked','other'],['--prelinked=other'],['--root'],['--root='],['--unknown']):
   self.profile['extra_args']=extra
   with self.assertRaises(ValueError):build.guard_prelink(self.root,self.env([entry('root')]))
  self.profile['extra_args']=['--root','child','--early-security-init=child=0x100']
  report=build.guard_prelink(self.root,self.env([entry('root'),entry('child')]))
  self.assertEqual(report['selected'],['child','root'])
 def test_missing_duplicate_malformed_and_wrong_cache_refused(self):
  for entries in ([entry('root',['absent'])],[entry('root'),entry('root')],[{'CFBundleIdentifier':'root','OSBundleLibraries':['bad']}],[]):
   with self.assertRaises(ValueError):build.guard_prelink(self.root,self.env(entries))
  env=self.env([entry('root')]);self.cache.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'hash'):build.guard_prelink(self.root,env)
  with self.assertRaisesRegex(ValueError,'official'):build.guard_prelink(self.root,{'A04BR3_WIFI':'1'})
 def test_registered_before_compilation_and_manual_builder_not_bypassed(self):
  import tomllib
  board=tomllib.loads((ROOT/'board.toml').read_text())
  self.assertIn('check-a04br3-wifi-prelink.py',board['kernel']['prelink']['archive_preflight'])
  source=(ROOT/'scripts/build-a04br3-board.py').read_text()
  self.assertLess(source.index('    guard_prelink(ROOT, os.environ)'),source.index("cxx_record = record("))
  with mock.patch.dict(os.environ,{'A04BR3_WIFI':'1'},clear=True):
   with self.assertRaises(ValueError):build.build_wifi(self.root,None,None,None,None,None,[],[],None,None)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--native-prelink-source',type=Path,required=True)
 args,remaining=parser.parse_known_args();SOURCE=args.native_prelink_source/'native_prelink.py'
 if not SOURCE.is_file():parser.error('canonical native_prelink.py is required')
 unittest.main(argv=[sys.argv[0],*remaining])
