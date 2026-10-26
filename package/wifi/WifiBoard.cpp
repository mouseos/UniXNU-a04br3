// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <libkern/c++/OSSymbol.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <sys/errno.h>
#include "a04br3_wlan_nvram.h"
#include "WifiEntropy.h"
#include <libkern/crypto/sha2.h>
#include <sys/random.h>
#include <kern/clock.h>
extern "C" vm_offset_t ml_io_map(vm_offset_t,vm_size_t);
extern "C" void thread_terminate_self(void);
extern "C" IOService *a04br3_wifi_allocate(void);
extern "C" uint64_t native_msdc_part_size(const char *);
extern "C" int native_msdc_part_read(const char *,uint64_t,void *,uint32_t);
namespace {
IOLock *entropyLock;
vm_offset_t entropyBase,topBase;
struct EntropyIO {
  unsigned read(unsigned off){auto v=*reinterpret_cast<volatile uint32_t *>(entropyBase+off);__asm__ volatile("dmb oshld":::"memory");return v;}
  void write(unsigned off,unsigned value){*reinterpret_cast<volatile uint32_t *>(entropyBase+off)=value;__asm__ volatile("dsb sy":::"memory");}
  unsigned topRead(unsigned off){auto v=*reinterpret_cast<volatile uint32_t *>(topBase+off);__asm__ volatile("dmb oshld":::"memory");return v;}
  void topWrite(unsigned off,unsigned value){*reinterpret_cast<volatile uint32_t *>(topBase+off)=value;__asm__ volatile("dsb sy":::"memory");}
  uint64_t now(){uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);return ns;}
};
int readCalibration(void *,uint32_t offset,void *out,unsigned bytes) {
  return native_msdc_part_read("nvram",offset,out,bytes)?0:EIO;
}
void worker(void *,wait_result_t) {
  auto *provider=IOService::getResourceService();
  auto *service=a04br3_wifi_allocate();
  bool initialized=service&&service->init();
  bool attached=initialized&&provider&&service->attach(provider);
  bool started=attached&&service->start(provider);
  if(!started){if(attached)service->detach(provider);if(service)service->release();}
  IOLog("a04br3 WiFi: service start=%u; radio/link results remain independent\n",unsigned(started));
  thread_terminate_self();
}
}
extern "C" int a04br3_wifi_random(uint8_t out[32]) {
  if(!out||!entropyLock||!entropyBase||!topBase)return EIO;
  uint8_t hardware[64]={},kernel[32]={},digest[32]={};EntropyIO io;
  IOLockLock(entropyLock);bool ok=a04_wifi_entropy(io,hardware);IOLockUnlock(entropyLock);
  if(!ok)return EIO;
  read_random(kernel,sizeof(kernel)); // outside SDIO/TRNG locks
  SHA256_CTX hash;SHA256_Init(&hash);SHA256_Update(&hash,hardware,sizeof(hardware));
  SHA256_Update(&hash,kernel,sizeof(kernel));SHA256_Final(digest,&hash);
  memcpy(out,digest,sizeof(digest));
  volatile uint8_t *p=hardware;for(unsigned i=0;i<sizeof(hardware);i++)p[i]=0;
  p=kernel;for(unsigned i=0;i<sizeof(kernel);i++)p[i]=0;
  p=digest;for(unsigned i=0;i<sizeof(digest);i++)p[i]=0;
  p=reinterpret_cast<volatile uint8_t *>(&hash);for(unsigned i=0;i<sizeof(hash);i++)p[i]=0;
  return 0;
}
extern "C" int a04br3_wlan_read_calibration(void *out,unsigned capacity) {
  if(!out||capacity!=512)return EINVAL;
  uint64_t bytes=native_msdc_part_size("nvram");
  if(!bytes||bytes>0x4000000ULL)return ENXIO;
  auto *work=static_cast<a04_nvram_work *>(IOMalloc(sizeof(a04_nvram_work)));
  if(!work)return ENOMEM;
  bzero(work,sizeof(*work));
  a04_nvram_reader reader={nullptr,unsigned(bytes),readCalibration};
  int result=a04_nvram_wifi(&reader,work,static_cast<uint8_t *>(out));
  bzero(work,sizeof(*work));IOFree(work,sizeof(*work));
  IOLog("a04br3 WiFi: runtime NVRAM result=%d bytes=%u; no calibration writes\n",result,result?0u:512u);
  return result;
}
void a04br3_wifi_init(void) {
  unsigned enabled=0;
  if(!PE_parse_boot_argn("a04br3-wifi",&enabled,sizeof(enabled))||enabled!=1)return;
  static bool attempted=false;if(__atomic_exchange_n(&attempted,true,__ATOMIC_ACQ_REL))return;
  // Like NativePowerVRGpu, these source classes are initialized by the
  // kernel's ordinary __mod_init_func path. No private-vtable binding or late
  // object patch. Build integration refuses an original NetworkingFamily root.
  auto *symbol=OSSymbol::withCString("A04BR3SDIO");if(!symbol)return;
  bool registered=OSMetaClass::getMetaClassWithName(symbol)!=nullptr;symbol->release();
  if(!registered){IOLog("a04br3 WiFi: source class initialization missing\n");return;}
  entropyLock=IOLockAlloc();
  entropyBase=ml_io_map(0x1020c000,4096);topBase=ml_io_map(0x10000000,4096);
  if(!entropyLock||!entropyBase||!topBase){IOLog("a04br3 WiFi: entropy mapping unavailable; refusing start\n");return;}
  thread_t thread=nullptr;
  if(kernel_thread_start(worker,nullptr,&thread)==KERN_SUCCESS)thread_deallocate(thread);
}
