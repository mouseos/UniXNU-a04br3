#!/usr/bin/env python3
"""Verify recorded vendored bytes/licenses and self-contained fixture symlinks."""
from pathlib import Path
import hashlib,json,unittest
ROOT=Path(__file__).resolve().parents[2]
WIFI=ROOT/'package/wifi'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class Provenance(unittest.TestCase):
 def test_original_imports_or_explicit_adaptations(self):
  old=json.loads((WIFI/'IMPORT.sources.json').read_text());new=json.loads((WIFI/'PORT.sources.json').read_text())
  self.assertEqual(old['source_commit'],new['donor_commit'])
  adapted={r['path']:r for r in new['adapted_imports']};self.assertEqual(len(adapted),5)
  for path,row in old['files'].items():
   if path in adapted:
    self.assertEqual(row['sha256'],adapted[path]['original_sha256']);want=adapted[path]['vendored_sha256']
   else:want=row['sha256']
   self.assertEqual(digest(WIFI/path),want,path)
 def test_apple_bearssl_and_host_license_bytes(self):
  apple=WIFI/'network/apple';m=json.loads((apple/'SOURCE.json').read_text())
  for row in m['files']:self.assertEqual(digest(apple/row['path']),row.get('vendored_sha256',row['sha256']))
  for row in m['local_files']:self.assertEqual(digest(apple/row['path']),row['sha256'])
  port=json.loads((WIFI/'PORT.sources.json').read_text())
  for row in port['bearssl_files']+port['network_host_tests']:
   self.assertEqual(digest(WIFI/row['path']),row['vendored_sha256']);self.assertEqual(row['byte_identical'],row['original_sha256']==row['vendored_sha256'])
  f=WIFI/'host-fixture';m=json.loads((f/'SOURCE.json').read_text())
  for name,want in m['files'].items():
   found=list(f.rglob(name));self.assertEqual(len(found),1);self.assertEqual(digest(found[0]),want)
  donor=m['port_test_sources'];self.assertEqual(digest(f/donor['license']['vendored_path']),donor['license']['sha256'])
  for row in donor['files']:self.assertEqual(digest(f/row['path']),row['sha256'])
  self.assertIn('APPLE PUBLIC SOURCE LICENSE',(apple/'APPLE_LICENSE').read_text())
  self.assertIn('Permission is hereby granted',(WIFI/'bearssl/LICENSE.txt').read_text())
 def test_fixture_symlinks_cannot_escape_source_package(self):
  links=[p for p in WIFI.rglob('*') if p.is_symlink()];self.assertEqual(len(links),2)
  for p in links:self.assertTrue(p.resolve().is_relative_to(WIFI.resolve()));self.assertTrue(p.resolve().exists())
if __name__=='__main__':unittest.main()
