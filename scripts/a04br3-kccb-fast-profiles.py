#!/usr/bin/env python3
"""Derive an exact KCCB off/on argument pair from the recorded all-task boot."""
import argparse
import hashlib
import json

BASE_SHA256 = '4cd8ea9395d7f8c0b2e61dbee37de598ea9c894c10670d404c5d44a75c2cb9e9'
LIMIT = 1023


def profiles(line):
    if not isinstance(line, str):
        raise ValueError('kernel_args must be a string')
    try:
        raw = line.encode('ascii')
    except UnicodeEncodeError as exc:
        raise ValueError('kernel_args must be ASCII') from exc
    if hashlib.sha256(raw).hexdigest() != BASE_SHA256:
        raise ValueError('kernel_args do not match the pinned baseline')
    words = line.split(' ')
    if not all(words) or any(any(c in w for c in '\t\r\n\0') for w in words):
        raise ValueError('noncanonical kernel_args')
    if any(w.split('=', 1)[0] in ('a4kf', 'a04br3-kccb-fast') for w in words):
        raise ValueError('KCCB option already present')
    result = {mode: line + ' a4kf=' + value for mode, value in (('off', '0'), ('on', '1'))}
    if any(len(v.encode('ascii')) > LIMIT for v in result.values()):
        raise ValueError('kernel_args exceed 1023 bytes')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', help='baseline manifest containing kernel_args')
    args = parser.parse_args()
    with open(args.manifest, encoding='utf-8') as f:
        pair = profiles(json.load(f)['kernel_args'])
    print(json.dumps({'baseline_args_sha256': BASE_SHA256, 'kernel_args': pair,
                      'length_bytes': {k: len(v.encode('ascii')) for k, v in pair.items()},
                      'args_sha256': {k: hashlib.sha256(v.encode('ascii')).hexdigest() for k, v in pair.items()}}, indent=2))


if __name__ == '__main__':
    main()
