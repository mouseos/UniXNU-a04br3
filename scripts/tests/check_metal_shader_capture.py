#!/usr/bin/env python3
"""Exercise the owner capture constructor without a GPU or Apple SDK."""
import os
import json
import shutil
import sys
from pathlib import Path
import runpy
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = runpy.run_path(str(root / 'scripts/build-a04br3-metal-plugin.py'))
constructor = source['shader_capture_constructor']
for value in (-1, 1000001, '0'):
    try:
        constructor(value)
    except ValueError:
        pass
    else:
        raise AssertionError(value)
with tempfile.TemporaryDirectory(prefix='pvr-shader-capture-') as temp:
    path = Path(temp)
    program = path / 'test.c'
    executable = path / 'test'
    program.write_text('''
#include <assert.h>
#include <string.h>
#include <time.h>
#include <errno.h>
static unsigned sleeps;
static int failure_mode;
static int fake_sleep(const struct timespec *request, struct timespec *remain) {
    ++sleeps;
    if (failure_mode) { errno = EINVAL; return -1; }
    if (sleeps == 1) { errno = EINTR; *remain = *request; return -1; }
    return 0;
}
#define nanosleep fake_sleep
static int (*capture_output)(const char *);
static unsigned lines;
void metal2vulkan_shader_capture_set_output(int (*output)(const char *)) { capture_output = output; }
void native_metal_board_text(const char *line) { assert(strlen(line) <= 63); ++lines; }
''' + constructor(42) + '''
#include <assert.h>
#include <string.h>
int main(void) {
    assert(!strcmp(getenv("METAL2VULKAN_CAPTURE_SPIRV"), "1"));
    assert(!strcmp(getenv("METAL2VULKAN_CAPTURE_SPIRV_START"), "42"));
    assert(capture_output);
    assert(capture_output("0123456789012345678901234567890123456789012345678901234567890123456789") == 0);
    assert(lines == 3);
    assert(sleeps == 4);
    failure_mode = 1;
    assert(capture_output("0123456789012345678901234567890123456789012345678901234567890123456789") == -1);
    assert(lines == 3);
    assert(sleeps == 5);
}
''')
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Wall', '-Wextra', '-Werror', str(program), '-o', str(executable)], check=True)
    env = os.environ.copy()
    env.pop('METAL2VULKAN_CAPTURE_SPIRV', None)
    env.pop('METAL2VULKAN_CAPTURE_SPIRV_START', None)
    subprocess.run([str(executable)], env=env, check=True)
with tempfile.TemporaryDirectory(prefix='pvr-shader-builder-') as temp:
    root = Path(temp)
    (root / 'scripts').mkdir()
    shutil.copyfile(Path(source['__file__']), root / 'scripts/build-a04br3-metal-plugin.py')
    fake = root / 'scripts/build-vulkan-metal-plugin.py'
    fake.write_text('import json,os,sys\nopen(os.environ["CAPTURE_TEST_ARGS"],"w").write(json.dumps(sys.argv[1:]))\n')
    board = root / 'package/powervr/user/NativePowerVRMetalBoard.mm'
    board.parent.mkdir(parents=True)
    board.write_text('// synthetic fixture\n')
    driver = root / 'driver.dylib'
    driver.write_bytes(b'fixture')
    output = root / 'output'
    record = root / 'args.json'
    env = dict(os.environ, CAPTURE_TEST_ARGS=str(record))
    command = [sys.executable, str(root / 'scripts/build-a04br3-metal-plugin.py'), '--vulkan', str(driver), '--out', str(output)]
    subprocess.run(command, env=env, check=True)
    assert '--runtime-shader-capture' not in json.loads(record.read_text())
    assert not (root / 'output.spirv-capture-enable.mm').exists()
    subprocess.run(command + ['--shader-capture-start', '42'], env=env, check=True)
    args = json.loads(record.read_text())
    assert '--runtime-shader-capture' in args
    assert str(root / 'output.spirv-capture-enable.mm') in args
    assert '"42"' in (root / 'output.spirv-capture-enable.mm').read_text()
    bad = subprocess.run(command + ['--shader-capture-start', '-1'], env=env, capture_output=True)
    assert bad.returncode != 0
print('PASS diagnostic constructor, CLI propagation, defaults and ordinal bounds; no GPU')
