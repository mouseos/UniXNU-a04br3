#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Focused host/ARM64 regressions for the batch import warning corrections."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

APPLE = Path(__file__).resolve().parents[2] / 'package/wifi/network/apple'

def compile_code(code, runtime=False, target=()):
    compiler = shutil.which('clang++')
    if not compiler:
        raise AssertionError('clang++ is required')
    with tempfile.TemporaryDirectory() as tmp:
        source, binary = Path(tmp) / 'actual.cpp', Path(tmp) / 'check'
        source.write_text(code)
        flags = ['-std=gnu++20', '-Werror', '-Wdeprecated-anon-enum-enum-conversion', '-Wmisleading-indentation']
        libs = []
        if runtime:
            flags += ['-fsanitize=address,undefined', '-o', str(binary)]
            if sys.platform.startswith('linux'):
                flags += ['-nostdlib++']
                libs = ['-Wl,-l:libstdc++.so.6']
        else:
            flags += ['-fsyntax-only']
        result = subprocess.run([compiler, *flags, *target, str(source), *libs], capture_output=True, text=True)
        if runtime and result.returncode == 0:
            result = subprocess.run([str(binary)], capture_output=True, text=True)
        return result


def region(name, start, end):
    source = (APPLE / name).read_text()
    matches = []
    offset = 0
    while True:
        first = source.find(start, offset)
        if first == -1:
            return matches
        last = source.index(end, first) + len(end)
        matches.append(source[first:last])
        offset = last

class BatchWarnings(unittest.TestCase):
    def enum_code(self):
        header = (APPLE / 'IOOutputQueue.h').read_text()
        blocks = [re.search(r'enum\s*\{\s*' + name + r'\b.*?\};', header, re.S).group(0)
                  for name in ('kIOOutputStatusMask', 'kIOOutputCommandMask', 'kIOReturnOutputSuccess')]
        debugger = (APPLE / 'IOKernelDebugger.cpp').read_text()
        power = re.search(r'static_cast<IOPMPowerFlags>\(kIOPMDeviceUsable\)\s*\|\s*static_cast<IOPMPowerFlags>\(kIOPMNotPowerManaged\)', debugger).group(0)
        queue = (APPLE / 'IOOutputQueue.cpp').read_text()
        fast = re.findall(r'static_cast<UInt32>\(kIOOutputStatusAccepted\)\s*\|\s*static_cast<UInt32>\(kIOOutputCommandNone\)', queue)
        self.assertEqual(len(fast), 2)
        code = '''using UInt32 = __UINT32_TYPE__;
// XNU d8b802 IOPM.h: API type and the two separate enum groups.
using IOPMPowerFlags = unsigned long;
enum { kIOPMDeviceUsable = 0x8000 };
enum { kIOPMNotPowerManaged = 0x0800 };
'''
        code += '\n'.join(blocks) + '\nconstexpr IOPMPowerFlags mask = ' + power + ';\n'
        code += '''static_assert(mask == 0x8800, "power mask unchanged");
static_assert((0UL & mask) == 0 && (0x8000UL & mask) != 0 && (0x0800UL & mask) != 0 && (0x10000UL & mask) == 0, "usable predicate unchanged");
static_assert(kIOReturnOutputSuccess == 0 && kIOReturnOutputDropped == 1 && kIOReturnOutputStall == 0x102, "output codes unchanged");
'''
        for i, expression in enumerate(fast):
            code += f'constexpr UInt32 fast{i} = {expression};\nstatic_assert(fast{i} == 0, "fast path unchanged");\n'
        return code

    def test_batch_enum_host_and_arm64(self):
        for target in ((), ('--target=arm64-apple-ios16.0',)):
            result = compile_code(self.enum_code(), target=target)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_enum_expressions_reproduce_errors(self):
        original, count = re.subn(r'static_cast<(?:UInt32|IOPMPowerFlags)>\((\w+)\)', r'\1', self.enum_code())
        self.assertEqual(count, 12)  # power2 + queue header6 + two fast paths4
        result = compile_code(original)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('deprecated-anon-enum-enum-conversion', result.stderr)

    def guard_cases(self):
        allocation = region('IOMbufMemoryCursor.cpp', '        if (mbuf_getpacket( MBUF_DONTWAIT, &out ))', 'mbuf_setflags( out, mbuf_flags( out ) & ~MBUF_PKTHDR );')
        self.assertEqual(len(allocation), 1)
        thread = region('IONetworkInterface.cpp', '    if ( !_inputEventThreadCall )', '_privateLock = IOLockAlloc();')
        self.assertEqual(len(thread), 1)
        nmi = region('IONetworkInterface.cpp', '    if (_remote_NMI_len)\n    {\n        checkForRNMIPacket', '        feedPacketInputTap(packet);\n    }')
        self.assertEqual(len(nmi), 2)
        return allocation, thread, nmi

    def test_actual_guard_regions_arm64(self):
        allocation, thread, nmi = self.guard_cases()
        for kind, fragments in (('allocation', allocation), ('thread', thread), ('nmi', nmi)):
            for fragment in fragments:
                result = compile_code(self.guard_code(kind, fragment), target=('--target=arm64-apple-ios16.0',))
                self.assertEqual(result.returncode, 0, result.stderr)

    def guard_code(self, kind, fragment, runtime=False):
        preamble = 'static int calls = 0, taps = 0;\n'
        if kind == 'allocation':
            preamble += '''static int status = 0, out = 0;
enum { MBUF_DONTWAIT = 0, MBUF_PKTHDR = 2 };
static int mbuf_getpacket(int, int *) { return status; }
static int mbuf_flags(int) { return 2; }
static void mbuf_setflags(int, int) { ++calls; }
static int run() {
'''
            body = fragment + '\n return 1;\n}\n'
            cases = 'status=1; require(run()==0 && calls==0); status=0; require(run()==1 && calls==1);'
        elif kind == 'thread':
            preamble += '''static bool _inputEventThreadCall = false;
static void *_privateLock;
static void *IOLockAlloc() { ++calls; return nullptr; }
static bool run() {
'''
            body = fragment + '\n return true;\n fail: return false;\n}\n'
            cases = 'require(!run() && calls==0); _inputEventThreadCall=true; require(run() && calls==1);'
        else:
            preamble += '''static int _remote_NMI_len = 0, packet = 0, hdrlen = 0, _remote_NMI_pattern = 0;
static bool _inputFilterFunc = false;
static void checkForRNMIPacket(int,int,int,int) { ++calls; }
static void feedPacketInputTap(int) { ++taps; }
static void run() {
'''
            body = fragment + '\n}\n'
            cases = 'for(int remote=0;remote<2;++remote) for(int tap=0;tap<2;++tap) { calls=taps=0; _remote_NMI_len=remote; _inputFilterFunc=tap; run(); require(calls==remote && taps==tap); }'
        if runtime:
            body += '\nstatic void require(bool value) { if(!value) __builtin_trap(); }\nint main() { ' + cases + ' }\n'
        return preamble + body

    def test_guard_behaviors_asan_ubsan(self):
        allocation, thread, nmi = self.guard_cases()
        for kind, fragments in (('allocation', allocation), ('thread', thread), ('nmi', nmi)):
            for fragment in fragments:
                result = compile_code(self.guard_code(kind, fragment, runtime=True), runtime=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_guard_indentation_reproduces_errors(self):
        old = {
            'allocation': '        if (mbuf_getpacket( MBUF_DONTWAIT, &out ))\n\t\t\treturn 0;\n\t\tmbuf_setflags( out, mbuf_flags( out ) & ~MBUF_PKTHDR );',
            'thread': '    if ( !_inputEventThreadCall )\n        goto fail;\n\n\t_privateLock = IOLockAlloc();',
            'nmi': '    if (_remote_NMI_len)\n        checkForRNMIPacket(packet, hdrlen, _remote_NMI_len, _remote_NMI_pattern);\n\n    // input BPF tap\n\tif (_inputFilterFunc)\n\t\tfeedPacketInputTap(packet);',
        }
        for kind, fragment in old.items():
            result = compile_code(self.guard_code(kind, fragment))
            if kind == 'allocation':
                # Unequal tab columns do not warn on this Clang; braces are
                # preventive only and the runtime test proves the same guard.
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, kind)
                self.assertIn('misleading-indentation', result.stderr)

if __name__ == '__main__':
    unittest.main()
