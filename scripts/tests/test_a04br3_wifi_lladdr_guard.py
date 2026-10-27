#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Compile and execute the shipped LLADDR method with synthetic collaborators.

This checks actual control flow/indentation, not SDK ABI or full family linkage.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
DECLARATIONS = r'''
using size_t = __SIZE_TYPE__;
enum { kIOEthernetAddressSize = 6, EINVAL = 22, ENETDOWN = 50, kIOReturnSuccess = 0 };
constexpr const char *kIOMACAddress = "address";
struct OSData {
    unsigned char bytes[6] = {1,2,3,4,5,6};
    size_t getLength() const { return 6; }
    const void *getBytesNoCopy() const { return bytes; }
};
#define OSDynamicCast(type, object) (object)
#define bcopy(source, target, count) __builtin_memcpy(target, source, count)
#define DLOG(...) ((void)0)
static int networkStatus, networkCalls;
static int ifnet_set_lladdr(void *, const char *, int) { ++networkCalls; return networkStatus; }
struct IONetworkController {
    int status = 0, calls = 0;
    unsigned char last[6] = {};
    int setHardwareAddress(const void *address, int size) {
        ++calls;
        if (size == 6) __builtin_memcpy(last, address, 6);
        return status;
    }
};
struct IOEthernetInterface {
    bool _ctrEnabled = false;
    OSData *backup = nullptr;
    int propertyReads = 0, propertyWrites = 0;
    OSData *getProperty(const char *) { ++propertyReads; return backup; }
    void *getIfnet() { return this; }
    void setProperty(const char *, void *, int) { ++propertyWrites; }
    int syncSIOCSIFLLADDR(IONetworkController *, const char *, int);
};
'''
CASES = r'''
static void require(bool value) { if (!value) __builtin_trap(); }
int main() {
    const char address[6] = {7,8,9,10,11,12};
    OSData backup;
    IONetworkController controller;
    IOEthernetInterface interface;
    interface.backup = &backup;
    // Length validation precedes the disabled-interface guard.
    require(interface.syncSIOCSIFLLADDR(&controller, address, 5) == EINVAL);
    require(interface.propertyReads == 0 && controller.calls == 0 && networkCalls == 0);
    require(interface.syncSIOCSIFLLADDR(&controller, address, 6) == ENETDOWN);
    require(interface.propertyReads == 0 && controller.calls == 0 && networkCalls == 0);
    interface._ctrEnabled = true;
    require(interface.syncSIOCSIFLLADDR(&controller, address, 6) == 0);
    require(interface.propertyReads == 1 && interface.propertyWrites == 1);
    require(controller.calls == 1 && networkCalls == 1 && controller.last[0] == 7);
    // A rejected stack change restores the captured hardware address.
    networkStatus = 1;
    require(interface.syncSIOCSIFLLADDR(&controller, address, 6) == EINVAL);
    require(controller.calls == 3 && controller.last[0] == 1 && controller.last[5] == 6);
    require(networkCalls == 2 && interface.propertyWrites == 1);
    // No backup means no restoration call, preserving the original behavior.
    interface.backup = nullptr;
    require(interface.syncSIOCSIFLLADDR(&controller, address, 6) == EINVAL);
    require(controller.calls == 4 && networkCalls == 3 && interface.propertyWrites == 1);
    // Preserve the existing controller-failure return; this is a formatting fix.
    controller.status = 1;
    require(interface.syncSIOCSIFLLADDR(&controller, address, 6) == 0);
    require(controller.calls == 5 && networkCalls == 3 && interface.propertyWrites == 1);
}
'''

INIT_DECLARATIONS = r'''
struct OSObject {};
struct ExpansionData {};
struct IONetworkController {};
static ExpansionData storage;
static bool allocationSucceeds = false, superclassSucceeds = false;
static int superclassCalls = 0, ethernetInitializations = 0;
#define IOMallocType(type) (allocationSucceeds ? &storage : nullptr)
enum { IFT_ETHER = 6 };
struct Parent {
    bool init(IONetworkController *) { ++superclassCalls; return superclassSucceeds; }
};
struct IOEthernetInterface : Parent {
    using super = Parent;
    ExpansionData *_reserved = nullptr;
    void setInterfaceType(int) { ++ethernetInitializations; }
    bool init(IONetworkController *);
};
'''
INIT_CASES = r'''
static void require(bool value) { if (!value) __builtin_trap(); }
int main() {
    IOEthernetInterface interface;
    IONetworkController controller;
    require(!interface.init(&controller));
    require(superclassCalls == 0 && ethernetInitializations == 0);
    allocationSucceeds = true;
    require(!interface.init(&controller));
    require(superclassCalls == 1 && ethernetInitializations == 0);
    superclassSucceeds = true;
    require(interface.init(&controller));
    require(superclassCalls == 2 && ethernetInitializations == 1);
}
'''

class LLAddrGuard(unittest.TestCase):
    def setUp(self):
        self.compiler = shutil.which('clang++')
        self.assertIsNotNone(self.compiler, 'clang++ is required')
        source = (ROOT / 'package/wifi/network/apple/IOEthernetInterface.cpp').read_text()
        start = source.index('int IOEthernetInterface::syncSIOCSIFLLADDR(')
        end = source.index('//---------------------------------------------------------------------------', start)
        self.method = source[start:end]

    def compile(self, method, runtime=False, target=(), declarations=DECLARATIONS, cases=CASES):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'actual-lladdr.cpp'
            source.write_text(declarations + method + (cases if runtime else ''))
            command = [self.compiler, '-std=gnu++20', '-Werror', '-Wmisleading-indentation']
            binary = Path(tmp) / 'guard'
            command += ['-fsanitize=address,undefined', '-o', str(binary)] if runtime else ['-fsyntax-only']
            # Linux may have only the runtime SONAME, not the development
            # libstdc++.so symlink; sanitizer's C++ ABI still needs the library.
            libraries = []
            if runtime and sys.platform.startswith('linux'):
                command += ['-nostdlib++']
                libraries = ['-Wl,-l:libstdc++.so.6']
            result = subprocess.run([*command, *target, str(source), *libraries], capture_output=True, text=True)
            if runtime and result.returncode == 0:
                result = subprocess.run([str(binary)], capture_output=True, text=True)
            return result

    def init_fragment(self):
        source = (ROOT / 'package/wifi/network/apple/IOEthernetInterface.cpp').read_text()
        start = source.index('bool IOEthernetInterface::init(')
        end = source.index('setInterfaceType(IFT_ETHER);', start) + len('setInterfaceType(IFT_ETHER);')
        # Only the actual entry guards through the first initialization action.
        return source[start:end] + '\n    return true;\n}\n'

    def test_init_guards_branches_asan_ubsan(self):
        result = self.compile(self.init_fragment(), runtime=True,
                              declarations=INIT_DECLARATIONS, cases=INIT_CASES)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_init_guards_arm64(self):
        result = self.compile(self.init_fragment(), target=('--target=arm64-apple-ios16.0',),
                              declarations=INIT_DECLARATIONS)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_init_indentation_reproduces_diagnostic(self):
        fragment = self.init_fragment()
        fixed = '\tif( _reserved == 0 )\n\t{\n\t\treturn false;\n\t}\n\n\tif ( super::init(controller) == false )\n\t{\n\t\treturn false;\n\t}'
        original = '\tif( _reserved == 0 )\n\t\treturn false;\n\n    if ( super::init(controller) == false )\n    \treturn false;'
        self.assertEqual(fragment.count(fixed), 1)
        result = self.compile(fragment.replace(fixed, original), declarations=INIT_DECLARATIONS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('misleading-indentation', result.stderr)

    def test_actual_method_branches_asan_ubsan(self):
        result = self.compile(self.method, runtime=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_method_arm64(self):
        result = self.compile(self.method, target=('--target=arm64-apple-ios16.0',))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_indentation_reproduces_diagnostic(self):
        original, count = re.subn(r'\tif \(_ctrEnabled != true\)[^\n]*\n\t\{\n\t\treturn \(ENETDOWN\);\n\t\}',
                                  '   if (_ctrEnabled != true)    /* reject if interface is down */\n        return (ENETDOWN);',
                                  self.method)
        self.assertEqual(count, 1)
        result = self.compile(original)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('misleading-indentation', result.stderr)

if __name__ == '__main__':
    unittest.main()
