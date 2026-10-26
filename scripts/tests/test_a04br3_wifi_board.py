#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Compile actual board glue in a public IOKit-shaped context (not SDK link)."""
from pathlib import Path
import subprocess,tempfile
from test_a04br3_wifi_driver import write_headers
root=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);write_headers(p)
 (p/'IOKit/IOService.h').write_text('''#pragma once
#include <IOKit/IOLib.h>
using IOReturn=unsigned;
class OSSymbol;
class OSMetaClass {public:static const OSMetaClass *getMetaClassWithName(const OSSymbol *);static void *preModLoad(const char *);static IOReturn postModLoad(void *);};
class IOService {public:static IOService *getResourceService();bool init();bool attach(IOService *);bool start(IOService *);void detach(IOService *);void release();};
''')
 (p/'libkern/c++/OSSymbol.h').write_text('''#pragma once
class OSSymbol {public:static OSSymbol *withCString(const char *);void release();};
''')
 (p/'mach').mkdir()
 (p/'mach/kmod.h').write_text('struct kmod_info_t {char name[64];};\n')
 (p/'kern/thread.h').write_text('''#pragma once
using thread_t=void *;using wait_result_t=int;enum{KERN_SUCCESS=0};
extern "C" int kernel_thread_start(void(*)(void*,wait_result_t),void*,thread_t*);
extern "C" void thread_deallocate(thread_t);
// thread_terminate_self deliberately absent, as in the actual IOKit context.
''')
 with (p/'kern/clock.h').open('a') as f:f.write('\nextern "C" uint64_t mach_absolute_time();extern "C" void absolutetime_to_nanoseconds(uint64_t,uint64_t*);\n')
 subprocess.run(['clang++-18','--target=arm64-apple-darwin','-std=c++17','-ffreestanding','-fno-exceptions','-fno-rtti','-Wall','-Wextra','-Werror','-I',str(p),'-c',str(root/'package/wifi/WifiBoard.cpp'),'-o',str(p/'board.o')],check=True)
print('PASS actual WifiBoard arm64 compile: public-context thread prototype, NVRAM API, entropy binding; SDK link not tested')
