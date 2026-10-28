#!/usr/bin/env python3
"""Build/sign an explicit owner diagnostic only; never install, launch or flash."""
import argparse,hashlib,json,plistlib,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if a.out.exists():raise SystemExit('Use a fresh output directory')
 source=ROOT/'tools/a04br3-network-bench.c'
 controlled=['tools/a04br3-network-bench.c','scripts/build-a04br3-network-bench.py']
 dirty=subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain','--',*controlled],text=True)
 if dirty:raise SystemExit('Commit diagnostic source and builder before SDK build')
 tree_dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True))
 sdk=subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
 version=subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-version'],text=True).strip()
 a.out.mkdir(parents=True);binary=a.out/'a04br3-network-bench';ent=a.out/'entitlements.plist'
 ent.write_bytes(plistlib.dumps({'com.apple.private.security.no-container':True}))
 command=['xcrun','clang','-target','arm64-apple-ios17.0','-isysroot',sdk,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)]
 subprocess.run(command,check=True)
 subprocess.run(['codesign','--force','--sign','-','--pagesize','4096','--entitlements',str(ent),str(binary)],check=True)
 subprocess.run(['codesign','--verify','--strict',str(binary)],check=True)
 details=subprocess.check_output(['codesign','-d','--verbose=4',str(binary)],stderr=subprocess.STDOUT,text=True)
 cd=re.search(r'^CDHash=([0-9a-f]{40})$',details,re.M)
 if not cd:raise SystemExit('Signed CDHash unavailable')
 ref=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
 result=dict(source_commit=ref,source_sha256=sha(source),builder_sha256=sha(Path(__file__)),source_tree_dirty=tree_dirty,binary_sha256=sha(binary),cdhash=cd[1],sdk=sdk,sdk_version=version,command=command,installed=False,hardware_tested=False)
 (a.out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
