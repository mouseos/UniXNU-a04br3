#!/usr/bin/env python3
"""Exercise wrapper forwarding, publication and failure handling offline."""
import os
from pathlib import Path
import subprocess
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / 'a04br3-read-batch-iostat-wrapper.sh'
with tempfile.TemporaryDirectory(prefix='iostat-wrapper-test-') as directory:
    root=Path(directory); bindir=root/'bin'; bindir.mkdir();calls=root/'calls'
    gh=bindir/'gh';gh.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$GH_CALLS"
if [ "$2" = view ]; then printf '%s\\n' "$GH_ASSETS"; exit 0; fi
case "$*" in *"$GH_FAIL"*) [ -n "$GH_FAIL" ] && exit 1;; esac
for arg in "$@"; do case "$arg" in /*) [ -f "$arg" ] || exit 2;; esac; done
exit 0
''');gh.chmod(0o755)
    def run(attempt,exitcode=0,fail='',assets='{"assets":[]}'):
        task=root/attempt;task.mkdir();prefix='io-batch-iostat-'+attempt
        runner=task/'runner.sh';runner.write_text('#!/bin/sh\n[ "$1" = "$IO_TASK" ] || exit 41\nmkdir "$IO_TASK/pair"\nexit '+str(exitcode)+'\n')
        (task/(prefix+'-run.log')).write_text('fixture')
        env={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH'],'IO_TASK':str(task),'IO_ATTEMPT':attempt,'RELEASE':'test','GITHUB_REPOSITORY':'fixture/test','GH_CALLS':str(calls),'GH_FAIL':fail,'GH_ASSETS':assets}
        result=subprocess.run(['bash',str(SCRIPT)],env=env,capture_output=True,text=True)
        return task,prefix,result
    task,prefix,r=run('success');assert r.returncode==0,r.stderr
    assert (task/(prefix+'-status.txt')).read_text()=='exit=0\n' and (task/(prefix+'-artifacts.tgz')).is_file()
    task,prefix,r=run('failed',exitcode=7);assert r.returncode==7 and (task/(prefix+'-status.txt')).read_text()=='exit=7\n'
    assert not (task/(prefix+'-artifacts.tgz')).exists()
    task,prefix,r=run('logfail',fail='logfail-run.log');assert r.returncode==74 and (task/(prefix+'-status.txt')).read_text()=='exit=74\n'
    task,prefix,r=run('statusfail',fail='statusfail-status.txt');assert r.returncode==74 and (task/(prefix+'-status.txt')).read_text()=='exit=74\n'
    task,prefix,r=run('archivefail',fail='archivefail-artifacts.tgz');assert r.returncode==74 and (task/(prefix+'-status.txt')).read_text()=='exit=74\n'
    task,prefix,r=run('invalidjson',assets='not json');assert r.returncode==73 and not (task/'pair').exists()
    task,prefix,r=run('collision',assets='{"assets":[{"name":"io-batch-iostat-collision-status.txt"}]}');assert r.returncode==73
    assert not (task/'pair').exists()
    lines=calls.read_text().splitlines()
    assert not any('failed-artifacts.tgz' in line for line in lines)
    assert not any('collision-artifacts.tgz' in line for line in lines)
    assert not any('logfail-status.txt' in line for line in lines)
print('PASS wrapper task forwarding, asset collision, status, log and failure publication')
