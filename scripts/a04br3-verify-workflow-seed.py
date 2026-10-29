#!/usr/bin/env python3
"""Verify the workflow Data seed identity against the kernel build environment."""
import json,os,sys,uuid
from pathlib import Path

def verify(path, expected):
    data=json.loads(Path(path).read_text())
    actual=uuid.UUID(data['volume_uuid'])
    target=uuid.UUID(expected)
    if not actual.int or not target.int or actual!=target:
        raise ValueError('workflow Data seed UUID differs from kernel build UUID')

def main():
    try:
        verify(Path(os.environ['RUNNER_TEMP'])/'owner-data/seed.json', os.environ['XNU_OWNER_DATA_VOLUME_UUID'])
    except (KeyError,ValueError,TypeError,OSError,json.JSONDecodeError) as exc:
        raise SystemExit('workflow Data seed verification failed: '+str(exc))
    print('PASS workflow Data seed identity matches kernel build environment')

if __name__=='__main__':main()
