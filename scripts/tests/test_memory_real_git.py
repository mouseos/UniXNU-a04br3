#!/usr/bin/env python3
"""Use a real Git checkout to check generated-output and source boundaries."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('memory_manifest', ROOT/'scripts/a04br3-memory-manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)

with tempfile.TemporaryDirectory(prefix='memory-real-git-') as tmp:
    repo = Path(tmp)/'source'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    identity = subprocess.check_output(['git', '-C', str(repo), 'config', 'user.email'], text=True).strip()
    assert identity.endswith('@users.noreply.github.com')
    (repo/'source.c').write_text('original\n')
    (repo/'tracked').mkdir()
    (repo/'tracked/keep.c').write_text('tracked\n')
    subprocess.run(['git','-C',str(repo),'add','source.c','tracked/keep.c'], check=True)
    subprocess.run(['git','-C',str(repo),'commit','-qm','Add test sources'], check=True)
    output = repo/'custom output'
    runner = repo/'build inputs'
    (output/'a04br3/tree').mkdir(parents=True)
    (output/'a04br3/tree/generated.txt').write_text('build')
    nested = output/'a04br3/tree/nested-repository'
    subprocess.run(['git', 'init', '-q', str(nested)], check=True)
    (nested/'generated').write_text('nested source')
    runner.mkdir()
    (runner/'data.dbr').write_text('generated seed')
    baseline = manifest.repo_identity(repo, generated_roots=(output, runner))
    assert baseline['commit']
    def reject():
        try:
            manifest.repo_identity(repo, generated_roots=(output, runner))
        except ValueError:
            return
        raise AssertionError('unexpectedly accepted source changes')
    (repo/'outside.c').write_text('untracked')
    reject()
    (repo/'outside.c').unlink()
    (repo/'source.c').write_text('modified')
    reject()
    subprocess.run(['git','-C',str(repo),'checkout','--','source.c'], check=True)
    (repo/'source.c').write_text('staged')
    subprocess.run(['git','-C',str(repo),'add','source.c'], check=True)
    reject()
    subprocess.run(['git','-C',str(repo),'reset','-q','HEAD','--','source.c'], check=True)
    subprocess.run(['git','-C',str(repo),'checkout','--','source.c'], check=True)
    assert manifest.repo_identity(repo, generated_roots=(output, runner)) == baseline
    for unsafe in (repo, repo/'tracked', repo/'source.c'):
        try:
            manifest.repo_identity(repo, generated_roots=(unsafe, runner))
        except ValueError:
            pass
        else:
            raise AssertionError('accepted a source-containing generated root')
    # An untracked symlink outside the generated tree is not hidden by its target.
    (repo/'outside-link').symlink_to(output, target_is_directory=True)
    reject()
print('PASS: real git checkout excludes only generated roots, preserves tracked and untracked source checks')
