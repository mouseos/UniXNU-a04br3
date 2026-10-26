"""Execute layout-sensitive prebuild/PMGR source blocks without network, SDK or device access."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile

repo = Path(__file__).resolve().parents[2]
prebuild = (repo/'scripts/a04br3-pre-build.sh').read_text()
start = prebuild.index('./unixnu tree -D')
block = prebuild[start:prebuild.index('\nMOUNT=', start)]
pmgr = (repo/'scripts/build-platform-power.py').read_text()
cache_start = pmgr.index('original = ROOT /')
cache_block = pmgr[cache_start:pmgr.index("print(run(['python3', ROOT / 'scripts/audit-amfi-pmgr-contract.py'", cache_start)]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    cli = root/'unixnu'
    cli.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
assert sys.argv[1:2] in [['tree'], ['status']]
assert sys.argv[-2:]==['-D',os.environ['DEVICE_DIR']]
if sys.argv[1]=='status': print(json.dumps({'output':os.environ['FIXTURE_OUTPUT']}))
''')
    cli.chmod(0o755)
    cache = root/'custom output/ipsw/21H461/kernelcache.decompressed'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'pinned kernelcache fixture')
    for schema in (1, 2):
        work = root/'custom output/a04br3'
        if schema == 2:
            work /= 'ipados-17.7.11-21H461/a04br3'
        tree = work/'tree'
        (tree/'scripts').mkdir(parents=True)
        env = {**os.environ, 'DEVICE_DIR':str(repo), 'FIXTURE_OUTPUT':str(work)}
        found = subprocess.check_output(['bash','-euc',block+'\nprintf "%s" "$TREE"'],cwd=root,env=env,text=True)
        assert found == str(tree), (schema, found)
        ns = dict(ROOT=tree, Path=Path, os=type('Env', (), {'environ':{'UNIXNU_OUT':str(root/'custom output')}}),
                  hashlib=hashlib, ORIGINAL_KERNELCACHE_SHA256=hashlib.sha256(cache.read_bytes()).hexdigest(),
                  require=lambda ok,why: None if ok else (_ for _ in ()).throw(AssertionError(why)))
        exec(compile(cache_block, '<actual PMGR cache source>', 'exec'), ns)
        assert ns['original'].resolve() == cache
    # A cache with the right filename but wrong content must still be refused.
    (tree/'.cache/firmware/kernelcache.macho/kernelcache.release.ipad7.decompressed').unlink()
    ns['ORIGINAL_KERNELCACHE_SHA256'] = '0'*64
    try:
        exec(compile(cache_block, '<actual PMGR cache source>', 'exec'), ns)
    except AssertionError:
        pass
    else:
        raise AssertionError('unqualified kernelcache accepted')
assert './unixnu build kernel -D "$DEVICE_DIR"' in prebuild
print('PASS actual shell tree selection and PMGR cache source for schema1/schema2/custom output with spaces; wrong cache hash refused; initial kernel pass retained')
