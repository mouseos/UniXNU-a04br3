#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Compile the shipped AVB option expression, including the original regression."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'package/wifi/network/apple/IOEthernetController.cpp'
# XNU d8b80295118ef25ac3a784134bcf95cd8e88109f:
# IOTypes.h: IOOptionBits is UInt32; IOMemoryDescriptor.h direction=3;
# IOBufferMemoryDescriptor.h physically-contiguous=0x10;
# IOMapTypes.h write-through=2 shifted by 8. Separate anonymous enum types
# reproduce the actual compiler diagnostic; no SDK include mocks are linked.
DECLARATIONS = '''
typedef __UINT32_TYPE__ IOOptionBits;
enum { kIODirectionIn = 1, kIODirectionOut = 2,
       kIODirectionOutIn = kIODirectionOut | kIODirectionIn };
enum { kIOMemoryPhysicallyContiguous = 0x10 };
enum { kIOWriteThruCache = 2, kIOMapCacheShift = 8,
       kIOMapWriteThruCache = kIOWriteThruCache << kIOMapCacheShift };
'''

class EnumOptions(unittest.TestCase):
    def setUp(self):
        self.compiler = shutil.which('clang++')
        self.assertIsNotNone(self.compiler, 'clang++ is required')
        source = SOURCE.read_text().split('IOEthernetController::allocateAVBPacket(', 1)[1]
        self.expression = re.search(r'IOOptionBits options\s*=\s*(.*?);', source, re.S).group(1)

    def compile(self, expression, target=()):
        code = DECLARATIONS + '\nconstexpr IOOptionBits options = ' + expression + ';\n'
        code += '''
static_assert(sizeof(IOOptionBits) == 4, "IOOptionBits width");
static_assert(options == 0x213, "preserve original direction/contiguous/cache bits");
static_assert((options & 3) == 3, "OutIn");
static_assert((options & 0x10) == 0x10, "physically contiguous");
static_assert((options & 0xf00) == 0x200, "write-through cache");
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'actual-options.cpp'
            path.write_text(code)
            return subprocess.run([self.compiler, '-std=gnu++20', '-Werror',
                                   '-Wdeprecated-anon-enum-enum-conversion', '-fsyntax-only',
                                   *target, str(path)], text=True, capture_output=True)

    def test_shipped_expression_host(self):
        result = self.compile(self.expression)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shipped_expression_arm64(self):
        result = self.compile(self.expression, ('--target=arm64-apple-ios16.0',))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_uncast_expression_reproduces_error(self):
        original, count = re.subn(r'static_cast<IOOptionBits>\((\w+)\)', r'\1', self.expression)
        self.assertEqual(count, 3)
        result = self.compile(original)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('deprecated-anon-enum-enum-conversion', result.stderr)

if __name__ == '__main__':
    unittest.main()
