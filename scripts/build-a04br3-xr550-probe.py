#!/usr/bin/env python3
"""Build/sign the opt-in XR550 boot probe; trust.json is for --extra-trust."""
import argparse
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
source = root/'package/powervr/xr550-probe'
subprocess.run(['bash',str(source/'build.sh'),str(out)],check=True)
spec = importlib.util.spec_from_file_location('pvrbuild',root/'scripts/build-a04br3-powervr-probe.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
binary = out/'xr550-probe'
module.check_macho(binary)
entitlements = out/'entitlements.plist'
entitlements.write_bytes(plistlib.dumps(module.ENTITLEMENTS))
cdhash = module.sign(binary,'org.unixnu.a04br3.xr550-probe',entitlements)
actual = plistlib.loads(subprocess.check_output(['codesign','-d','--entitlements',':-',str(binary)],stderr=subprocess.DEVNULL))
if actual != module.ENTITLEMENTS:
    raise SystemExit('signed entitlement set differs')
(out/'trust.json').write_text(json.dumps({'daemon_cdhash':cdhash},indent=2)+'\n')
print('XR550 signed and verified; use trust.json with --extra-trust')
