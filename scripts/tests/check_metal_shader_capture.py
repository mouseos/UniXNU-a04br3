#!/usr/bin/env python3
"""Exercise the owner capture constructor without a GPU or Apple SDK."""
import os
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
static void (*capture_output)(const char *);
static unsigned lines;
void metal2vulkan_shader_capture_set_output(void (*output)(const char *)) { capture_output = output; }
void native_metal_board_text(const char *line) { assert(strlen(line) <= 63); ++lines; }
''' + constructor(42) + '''
#include <assert.h>
#include <string.h>
int main(void) {
    assert(!strcmp(getenv("METAL2VULKAN_CAPTURE_SPIRV"), "1"));
    assert(!strcmp(getenv("METAL2VULKAN_CAPTURE_SPIRV_START"), "42"));
    assert(capture_output);
    capture_output("0123456789012345678901234567890123456789012345678901234567890123456789");
    assert(lines == 2);
}
''')
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Wall', '-Wextra', '-Werror', str(program), '-o', str(executable)], check=True)
    env = os.environ.copy()
    env.pop('METAL2VULKAN_CAPTURE_SPIRV', None)
    env.pop('METAL2VULKAN_CAPTURE_SPIRV_START', None)
    subprocess.run([str(executable)], env=env, check=True)
print('PASS diagnostic constructor and ordinal bounds; no GPU')
