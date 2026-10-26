# SPDX-License-Identifier: MIT
"""Compile actual SDIO CPP for arm64. Declaration shim only; no XNU link."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SDIO = Path(__file__).resolve().parents[2] / 'package/wifi/sdio'

def write_headers(p):
    for name in ('IOKit', 'libkern/c++', 'libkern/crypto', 'pexpert/arm64', 'kern', 'net', 'sys'):
        (p / name).mkdir(parents=True, exist_ok=True)
    (p/'IOKit/IOLib.h').write_text('''#pragma once
#include <stdint.h>
#include <stddef.h>
using vm_offset_t=uintptr_t;using vm_size_t=uintptr_t;
struct IOLock;
extern "C" IOLock *IOLockAlloc();extern "C" void IOLockFree(IOLock *);
extern "C" void IOLockLock(IOLock *);extern "C" void IOLockUnlock(IOLock *);
extern "C" void *IOMalloc(size_t);extern "C" void IOFree(void *,size_t);
extern "C" void bcopy(const void *,void *,size_t);
extern "C" void bzero(void *,size_t);
extern "C" void IOSleep(unsigned);extern "C" void IODelay(unsigned);
extern "C" void IOLog(const char *,...) __attribute__((format(printf,1,2)));
''')
    (p/'IOKit/IOService.h').write_text('''#pragma once
#include <IOKit/IOLib.h>
#define OSDeclareDefaultStructors(c)
#define OSDefineMetaClassAndStructors(c,b) static_assert(sizeof(c)<=16U*1024U,"real IOKit object size ceiling");
#define APPLE_KEXT_OVERRIDE override
#define OSDynamicCast(c,p) static_cast<c *>(p)
class OSObject {public:void retain();void release();};
class OSBoolean:public OSObject {};extern OSBoolean *kOSBooleanFalse,*kOSBooleanTrue;
class IOService:public OSObject {public:
 virtual bool start(IOService *);virtual void stop(IOService *);virtual void free();
 bool setProperty(const char *,OSBoolean *);bool setProperty(const char *,uint64_t,unsigned);
 OSObject *copyProperty(const char *);void registerService();
};
''')
    (p/'libkern/c++/OSData.h').write_text('''#pragma once
#include <IOKit/IOService.h>
class OSData:public OSObject {public:unsigned getLength();const void *getBytesNoCopy();};
''')
    (p/'libkern/crypto/sha2.h').write_text('''#pragma once
#define SHA256_DIGEST_LENGTH 32
struct SHA256_CTX {unsigned char opaque[256];};
extern "C" void SHA256_Init(SHA256_CTX *);
extern "C" void SHA256_Update(SHA256_CTX *,const void *,size_t);
extern "C" void SHA256_Final(void *,SHA256_CTX *);
''')
    (p/'pexpert/pexpert.h').write_text('''#pragma once
extern "C" int PE_parse_boot_argn(const char *,void *,int);
struct PE_state_t {void *bootArgs;};extern PE_state_t PE_state;
''')
    (p/'pexpert/device_tree.h').write_text('''#pragma once
using DTEntry=const void *;enum{kSuccess=1};
extern "C" int SecureDTLookupEntry(DTEntry,const char *,DTEntry *);
extern "C" int SecureDTGetProperty(DTEntry,const char *,const void **,unsigned *);
''')
    (p/'pexpert/arm64/boot.h').write_text('''#pragma once
struct boot_args {uint64_t physBase,memSize,topOfKernelData;};
''')
    (p/'kern/thread_call.h').write_text('#pragma once\nusing thread_call_param_t=void *;using thread_call_t=void *;\nenum {THREAD_CALL_PRIORITY_KERNEL=0,THREAD_CALL_OPTIONS_ONCE=1};\nextern thread_call_t thread_call_allocate_with_options(void (*)(void *,void *),void *,unsigned,unsigned);\nextern int thread_call_cancel_wait(thread_call_t);extern int thread_call_free(thread_call_t);\nextern int thread_call_enter_delayed(thread_call_t,uint64_t);\n')
    (p/'kern/clock.h').write_text('#pragma once\nusing clock_sec_t=unsigned;using clock_usec_t=unsigned;\nextern void clock_get_system_microtime(clock_sec_t *,clock_usec_t *);\nextern void clock_interval_to_deadline(unsigned,unsigned,uint64_t *);\n')
    (p/'net/init.h').write_text('extern int net_init_add(void (*)(void));\n')
    (p/'sys/kpi_mbuf.h').write_text('#include <stddef.h>\ntypedef void *mbuf_t;\nextern size_t mbuf_pkthdr_len(mbuf_t);extern int mbuf_copydata(mbuf_t,size_t,size_t,void *);extern void mbuf_freem(mbuf_t);\n')
    (p/'string.h').write_text('#pragma once\n#include <stddef.h>\nextern "C" void *memcpy(void *,const void *,size_t);\nextern "C" void *memset(void *,int,size_t);\nextern "C" void *memmove(void *,const void *,size_t);\nextern "C" int memcmp(const void *,const void *,size_t);\n')
    (p/'sys/types.h').write_text('#pragma once\ntypedef unsigned int u_int;\n')
    (p/'sys/random.h').write_text('#pragma once\n#include <sys/types.h>\nextern "C" void read_random(void *,u_int);\n')
    (p/'sys/errno.h').write_text('#pragma once\nenum {EAGAIN=35,EINVAL=22,ENXIO=6,ENOMEM=12,EALREADY=37,EBUSY=16,EOPNOTSUPP=102,ENOBUFS=55,EMSGSIZE=40,ENETDOWN=50,EIO=5,ENOENT=2,ESTALE=70,ENOSPC=28,ENOTCONN=57,EOVERFLOW=84};\n')

class WLANDriverCompile(unittest.TestCase):
    def test_arm64_cpp_including_wmt_and_wlan_owned_boot_path(self):
        clang = shutil.which('clang++')
        self.assertIsNotNone(clang)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            write_headers(p)
            subprocess.run([clang, '--target=arm64-apple-darwin', '-std=c++14',
                            '-DKERNEL', '-DA04BR3_WIFI=1', '-ffreestanding', '-fno-exceptions', '-fno-rtti', '-O2',
                            '-Wall', '-Wextra', '-Werror', '-Wframe-larger-than=8192',
                            '-fstack-usage', '-I', str(p), '-c', str(SDIO/'A04BR3SDIO.cpp'),
                            '-o', str(p/'driver.o')], check=True)
            print((p/'driver.su').read_text(), end='')

if __name__ == '__main__':
    unittest.main()
