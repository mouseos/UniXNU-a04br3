// SPDX-License-Identifier: MIT
// The PowerVR GE8300 for the original Metal discovery: publishes the accelerator (NativePowerVRAccelerator.cpp, staged) that
// backboardd's Metal discovery finds, and serves its connections. The plugin is UniXNU's vendor-neutral NativeMetal plugin
// (drivers/NativeMetal/NativeVulkanMetalPlugin.mm) built with this board's hooks (package/powervr/user/NativePowerVRMetalBoard.mm) and
// Mesa's PowerVR Vulkan driver; it reaches the GPU through the connections below with the NativePowerVR ABI (NativePowerVRABI.h).
//
// Publication, as UniXNU-sheng package/GpuPowerProbe.cpp publishMetal() does for the Adreno accelerator: the original IOGPUFamily kext
// is loaded (it is in [kernel.prelink] roots), the staged class is registered between OSMetaClass::preModLoad() and postModLoad(),
// and one object is attached to the platform expert with
//   MetalPluginName           NativePowerVRMetal   (the bundle arrives through the GPU addon union mount, sheng-gpu-addon=1)
//   MetalPluginClassName      NativePowerVRMetalDevice
//   NativeSandboxGrantProcess backboardd, or the boot argument a04br3-gpu-metal-grant=<names> (UniXNU patch
//                             zzw-native-adreno-sandbox-grant: the kernel's open path grants the accelerator's user client to these
//                             processes in place of the sandbox verdict; the original profiles name no class of this driver)
// It runs from the board launch hook (unixnu_board_launch(), before launchd) under a04br3-gpu-metal=1, on its own thread, which waits
// for NativePowerVRGpu to be published (the firmware boot thread of NativePowerVR.cpp) for up to 120 s.
//
// Connections (NativePowerVRABI.h): NativePowerVRMetalOpenType a GPU connection, NativePowerVRMetalHandoffOpenType the display
// hand-off only, NativePowerVRMetalMarkBase | value a progress marker or text character, logged as "powervr-metal[<process>]: ..." and
// refused. Other types (the original IOGPU library opens with type 1 and an API value) are refused. A process may open at most
// a04br3-gpu-metal-opens GPU connections per boot (default 40, 0 = unlimited), so a client that crashes on every launch cannot loop.
//
// a04br3-gpu-metal=2, or =1 with a04br3-gpu-metal-hide=1 (default off): publish, and the two plugin keys (MetalPluginName, MetalPluginClassName) read as absent in every process
// the grant does not name. Only the granted processes get a native device (the plugin returns nil elsewhere: NativeVulkanMetalPlugin.mm
// deviceForPort serves backboardd and the .clients list), yet without this every process that looks for Metal loads the whole plugin
// bundle before it learns that. With the keys absent, the original discovery (Metal _getMetalPluginClassForService returns no class
// when MetalPluginName is missing) skips the accelerator, the process loads nothing of ours and takes its own no-GPU path, as on a board
// without a Metal plugin. The first 64 refused reads go to the console ("MetalPluginName hidden from"). A process named in the plugin's
// .clients but not in the grant is refused too (it could not open the accelerator through the sandbox anyway).
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOPlatformExpert.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>
#include <pexpert/pexpert.h>
#include <mach/kmod.h>
#include <kern/thread.h>
#include <string.h>
#include <sys/errno.h>
#include <stdint.h>
#include "NativePowerVRNameList.h"

extern kmod_info_t g_kernel_kmod_info;
extern "C" {
kern_return_t OSKextLoadKextWithIdentifier(const char *);
void proc_selfname(char *, int);
int proc_selfpid(void);
kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
void thread_deallocate(thread_t);
// NativePowerVRGpu.cpp
IOReturn native_powervr_new_connection(IOService *provider, task_t owningTask, void *securityID, UInt32 type, OSDictionary *properties,
                                       bool handoffOnly, IOUserClient **handler);
bool native_powervr_published(void);
// NativePowerVRAccelerator.cpp, staged (its archive is added to the board archive by UniXNU build-native-adreno-accelerator.py --into).
// Weak: a board archive without it publishes nothing and says so.
void native_powervr_accelerator_static_init(void) __asm__("__GLOBAL__sub_I_NativePowerVRAccelerator.cpp") __attribute__((weak));
IOService *native_powervr_accelerator_allocate(void) __attribute__((weak));
unsigned native_powervr_accelerator_instance_size(void) __attribute__((weak));

void native_powervr_metal_publish(void);
IOReturn native_powervr_metal_new_user_client(IOService *, task_t, void *, UInt32, OSDictionary *, IOUserClient **);
bool native_powervr_metal_note_property_read(const char *);
// Stages: 1 thread started, 2 NativePowerVRGpu published, 3 IOGPUFamily loaded, 4 class registered, 5 accelerator published.
volatile uint32_t native_powervr_metal_stage, native_powervr_metal_opens, native_powervr_metal_refused, native_powervr_metal_name_reads,
    native_powervr_metal_hidden_reads;
volatile int native_powervr_metal_status;
}

// The constants of NativePowerVRABI.h, which needs the Linux uAPI first (as NativePowerVRGpu.cpp includes it).
#include "compat/native_powervr_linux_compat.h"
#include "native_powervr_uapi.h"
#include "NativePowerVRABI.h"
#undef min
#undef max

namespace {
IOLock *gLock;
// a04br3-gpu-metal-hide=1: the processes that see the plugin keys (the grant), set once before the accelerator is registered.
char gPluginVisibleTo[128];
bool gPluginHidden;

// The plugin's text lines and markers, per process (first four names).
struct Talker {char name[20]; char line[72]; unsigned length; uint32_t marks, gpuOpens;};
Talker gTalkers[4];
uint32_t gMarksLogged;

Talker *talker(const char *name)
{
  for (Talker &t : gTalkers) {
    if (!t.name[0]) { strlcpy(t.name, name, sizeof t.name); return &t; }
    if (!strncmp(t.name, name, sizeof t.name)) return &t;
  }
  return nullptr;
}

void flushLine(Talker &t)
{
  if (t.length) IOLog("powervr-metal[%s]: %s\n", t.name, t.line);
  t.length = 0;
  t.line[0] = 0;
}

// One marker or text character of the plugin (NativePowerVRABI.h). The first 512 markers of a boot are logged; text is always logged.
void mark(uint32_t value)
{
  char name[20] = {};
  proc_selfname(name, sizeof name);
  if (!gLock) return;
  IOLockLock(gLock);
  Talker *t = talker(name);
  if (t) {
    t->marks++;
    if (value == 0x7ffe || value == 0x7fff) {
      flushLine(*t);
    } else if (value == 0x7ffd) {
      flushLine(*t);
    } else if (value > 0x7000 && value < 0x7080) {
      if (t->length + 1 >= sizeof t->line) flushLine(*t);
      const char c = static_cast<char>(value & 0x7f);
      t->line[t->length++] = c >= 0x20 ? c : '?';
      t->line[t->length] = 0;
    } else if (gMarksLogged < 512) {
      gMarksLogged++;
      IOLog("powervr-metal[%s]: mark 0x%04x\n", t->name, value);
    }
  }
  IOLockUnlock(gLock);
}

unsigned bootArg(const char *name, unsigned fallback)
{
  unsigned value = fallback;
  PE_parse_boot_argn(name, &value, sizeof value);
  return value;
}

void publish(void)
{
  native_powervr_metal_stage = 1;
  for (unsigned waited = 0; !native_powervr_published(); waited++) {
    if (waited >= 1200) { native_powervr_metal_status = kIOReturnNotReady; IOLog("powervr-metal: NativePowerVRGpu not published in 120 s\n"); return; }
    IOSleep(100);
  }
  native_powervr_metal_stage = 2;
  if (!native_powervr_accelerator_static_init || !native_powervr_accelerator_allocate || !native_powervr_accelerator_instance_size) {
    native_powervr_metal_status = kIOReturnUnsupported;
    IOLog("powervr-metal: no staged accelerator in this kernel (build-native-adreno-accelerator.py --source ... --into)\n");
    return;
  }
  const kern_return_t kext = OSKextLoadKextWithIdentifier("com.apple.iokit.IOGPUFamily");
  if (kext) { native_powervr_metal_status = kext; IOLog("powervr-metal: IOGPUFamily load 0x%x\n", kext); return; }
  const OSSymbol *parentName = OSSymbol::withCString("IOAcceleratorES");
  const OSMetaClass *parent = parentName ? OSMetaClass::getMetaClassWithName(parentName) : nullptr;
  OSSafeReleaseNULL(parentName);
  if (!parent || parent->getClassSize() != 136) { native_powervr_metal_status = kIOReturnNotFound; IOLog("powervr-metal: no IOAcceleratorES\n"); return; }
  native_powervr_metal_stage = 3;
  void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
  if (!handle) { native_powervr_metal_status = kIOReturnNoResources; return; }
  native_powervr_accelerator_static_init();
  native_powervr_metal_status = OSMetaClass::postModLoad(handle);
  if (native_powervr_metal_status) { IOLog("powervr-metal: class registration 0x%x\n", native_powervr_metal_status); return; }
  if (native_powervr_accelerator_instance_size() != 136) { native_powervr_metal_status = kIOReturnInvalid; return; }
  native_powervr_metal_stage = 4;
  IOService *accelerator = native_powervr_accelerator_allocate();
  if (!accelerator || !accelerator->init()) {
    OSSafeReleaseNULL(accelerator);
    native_powervr_metal_status = kIOReturnNoMemory;
    return;
  }
  // The two keys the original _MTLRegisterDevices helper reads (UniXNU scripts/audit-target-metal-discovery.py). The plugin name is
  // ours: the bundle exists only through the addon mount, and no Apple device identity is claimed.
  accelerator->setProperty("MetalPluginName", "NativePowerVRMetal");
  accelerator->setProperty("MetalPluginClassName", "NativePowerVRMetalDevice");
  char grant[128] = "backboardd";
  PE_parse_boot_argn("a04br3-gpu-metal-grant", grant, sizeof grant);
  accelerator->setProperty("NativeSandboxGrantProcess", grant);
  if (bootArg("a04br3-gpu-metal", 0) == 2 || bootArg("a04br3-gpu-metal-hide", 0) == 1) {
    strlcpy(gPluginVisibleTo, grant, sizeof gPluginVisibleTo);
    __atomic_store_n(&gPluginHidden, true, __ATOMIC_RELEASE);
    IOLog("powervr-metal: plugin keys visible only to %s\n", gPluginVisibleTo);
  }
  if (!accelerator->attach(IOService::getPlatform())) {
    accelerator->release();
    native_powervr_metal_status = kIOReturnNotAttached;
    return;
  }
  accelerator->registerService();
  native_powervr_metal_stage = 5;
  IOLog("powervr-metal: accelerator published (MetalPluginName NativePowerVRMetal, grant %s)\n", grant);
}

void publishThread(void *, wait_result_t)
{
  publish();
  IOLog("powervr-metal: publication stage %u status 0x%x\n", native_powervr_metal_stage, static_cast<unsigned>(native_powervr_metal_status));
}
}  // namespace

// a04br3-gpu-metal=1 or 2 (from unixnu_board_launch(), A04BR3Board.cpp): publish once, on its own thread.
extern "C" void native_powervr_metal_publish(void)
{
  static bool started;
  const unsigned mode = bootArg("a04br3-gpu-metal", 0);
  if (started || (mode != 1 && mode != 2)) return;
  started = true;
  gLock = IOLockAlloc();
  if (!gLock) return;
  thread_t thread = nullptr;
  if (kernel_thread_start(publishThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

// Every property read of the accelerator (NativePowerVRAccelerator.cpp); false: the key reads as absent in this process.
extern "C" bool native_powervr_metal_note_property_read(const char *key)
{
  if (!key) return true;
  const bool pluginName = !strcmp(key, "MetalPluginName");
  if (!pluginName && strcmp(key, "MetalPluginClassName")) return true;
  char name[20] = {};
  proc_selfname(name, sizeof name);
  // Kernel reads (pid 0) always see the keys; only user processes outside the grant are refused.
  const bool visible = !__atomic_load_n(&gPluginHidden, __ATOMIC_ACQUIRE) || proc_selfpid() == 0 ||
                       native_powervr_name_listed(gPluginVisibleTo, name);
  if (pluginName) {
    // Unlocked counter: the discovery runs at process start; the first reads say which processes look for a Metal device.
    const uint32_t reads = native_powervr_metal_name_reads;
    native_powervr_metal_name_reads = reads + 1;
    if (reads < 32) IOLog("powervr-metal: MetalPluginName read by %s pid=%d%s\n", name, proc_selfpid(), visible ? "" : " (hidden)");
    if (!visible) {
      // Its own budget: the shared one above is spent by the first processes of a boot.
      const uint32_t hidden = native_powervr_metal_hidden_reads;
      native_powervr_metal_hidden_reads = hidden + 1;
      if (hidden < 64) IOLog("powervr-metal: MetalPluginName hidden from %s pid=%d (%u)\n", name, proc_selfpid(), hidden + 1);
    }
  }
  return visible;
}

extern "C" IOReturn native_powervr_metal_new_user_client(IOService *provider, task_t owningTask, void *securityID, UInt32 type,
                                                         OSDictionary *properties, IOUserClient **handler)
{
  if (!handler) return kIOReturnBadArgument;
  *handler = nullptr;
  if ((type & 0xffff0000u) == static_cast<uint32_t>(NativePowerVRMetalMarkBase) && (type & 0xffffu)) {
    mark(type & 0xffffu);
    return kIOReturnUnsupported;
  }
  if (type == NativePowerVRMetalHandoffOpenType)
    return native_powervr_new_connection(provider, owningTask, securityID, type, properties, true, handler);
  if (type != NativePowerVRMetalOpenType) {
    native_powervr_metal_refused = native_powervr_metal_refused + 1;
    return kIOReturnUnsupported;
  }
  char name[20] = {};
  proc_selfname(name, sizeof name);
  const unsigned cap = bootArg("a04br3-gpu-metal-opens", 40);
  if (gLock) {
    IOLockLock(gLock);
    Talker *t = talker(name);
    const bool capped = cap && t && t->gpuOpens >= cap;
    if (t && !capped) t->gpuOpens++;
    IOLockUnlock(gLock);
    if (capped) { native_powervr_metal_refused = native_powervr_metal_refused + 1; return kIOReturnBusy; }
  }
  const IOReturn status = native_powervr_new_connection(provider, owningTask, securityID, type, properties, false, handler);
  if (status == kIOReturnSuccess) native_powervr_metal_opens = native_powervr_metal_opens + 1;
  else native_powervr_metal_refused = native_powervr_metal_refused + 1;
  IOLog("powervr-metal: GPU connection for %s: 0x%x\n", name, static_cast<unsigned>(status));
  return status;
}

