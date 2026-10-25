// SPDX-License-Identifier: MIT
// The a04br3 hooks of UniXNU's vendor-neutral Metal plugin (drivers/NativeMetal/NativeMetalBoard.h, NativeVulkanMetalPlugin.mm), built
// into the plugin NativePowerVRMetal.bundle by scripts/build-a04br3-metal-plugin.py with Mesa's PowerVR Vulkan driver
// (patches/mesa/pvr-darwin-winsys.patch). Everything goes through the accelerator the original Metal discovery found (the port passed to
// initWithAcceleratorPort:, package/powervr/NativePowerVRAccelerator.cpp), whose open types are listed in NativePowerVRABI.h:
//  - the driver: Mesa's Darwin winsys opens IOServiceMatching("IOAcceleratorES") with NativePowerVRMetalOpenType (PVR_DARWIN_SERVICE,
//    PVR_DARWIN_OPEN_TYPE) and reaches the NativePowerVR ABI through the tunnel; the GE8300 is not on Mesa's conformant list, so
//    PVR_I_WANT_A_BROKEN_VULKAN_DRIVER is set, as powervrprobed_vulkan.c sets it;
//  - markers and text: IOServiceOpen with NativePowerVRMetalMarkBase | value, which the kernel logs on UART and refuses
//    (package/powervr/NativePowerVRMetal.cpp);
//  - the display hand-off (sheng-fb-gpu=1): a connection of NativePowerVRMetalHandoffOpenType, methods NativePowerVRMetalHandoffBase +
//    NGP_METHOD_* through the tunnel, the scanout as memory type NativePowerVRMetalScanoutType.
// IOKit is used through dlsym, as the Adreno plugin does (the iPhoneOS SDK has no IOKit headers).
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "NativeMetalBoard.h"
#include "NativeBridgeMetalFrontend.h"

namespace {
// NativePowerVRABI.h (kept in step; the header itself needs the Linux uAPI first).
constexpr uint32_t kTunnelMagic = 0x4e544e4c, kTunnelProbe = 0xffffffffu;
constexpr uint32_t kMetalOpenType = 0x5056, kHandoffOpenType = 0x4148, kMarkBase = 0x41470000u, kHandoffBase = 0x7000,
                   kScanoutType = 0x5343;
// IOKit's kIOMapAnywhere | kIOMapInhibitCache: the scanout is device memory, never cached by the CPU.
constexpr uint32_t kMapScanout = 0x1 | 0x100;

using OpenFn = int (*)(unsigned, unsigned, uint32_t, unsigned *);
using CloseFn = int (*)(unsigned);
using RetainFn = int (*)(unsigned);
using CallFn = int (*)(unsigned, uint32_t, const void *, size_t, void *, size_t *);
using MapFn = int (*)(unsigned, uint32_t, unsigned, uint64_t *, uint64_t *, uint32_t);

struct IOKit {
    OpenFn open = nullptr;
    CloseFn close = nullptr;
    CallFn call = nullptr;
    MapFn map = nullptr;
    unsigned port = 0;
};
IOKit gIOKit;
pthread_mutex_t gTextLock = PTHREAD_MUTEX_INITIALIZER;

// The hand-off connection: its admitted selector carries the tunnel.
struct Handoff {
    unsigned conn = 0;
    uint32_t selector = 0;
};
Handoff gHandoff;

int handoffCall(void *context, uint32_t method, const void *in, size_t inSize, void *out, size_t outSize) {
    Handoff *h = static_cast<Handoff *>(context);
    uint8_t buffer[8 + 64];
    if (inSize > sizeof buffer - 8 || !gIOKit.call) return -1;
    const uint32_t header[2] = {kTunnelMagic, kHandoffBase + method};
    memcpy(buffer, header, sizeof header);
    if (inSize) memcpy(buffer + 8, in, inSize);
    size_t outBytes = outSize;
    return gIOKit.call(h->conn, h->selector, buffer, 8 + inSize, outSize ? out : nullptr, outSize ? &outBytes : nullptr);
}
int handoffMap(void *context, uint64_t *address, uint64_t *length) {
    Handoff *h = static_cast<Handoff *>(context);
    return gIOKit.map ? gIOKit.map(h->conn, kScanoutType, mach_task_self(), address, length, kMapScanout) : -1;
}
void handoffMark(void *, uint32_t value) {native_metal_board_mark(value);}
// Only called after a real pipeline failure; field limits bound UART round trips.
void pipelineFailureField(unsigned sequence,const char *field,const char *text,size_t limit) {
    if(!text)text="<null>";
    size_t at=0;unsigned part=0;
    do {
        size_t count=0;while(count<48 && at+count<limit && text[at+count])++count;
        char line[64];snprintf(line,sizeof line,"PF%u %s%02u %.*s",sequence,field,part++,int(count),text+at);
        native_metal_board_text(line);at+=count;
    } while(at<limit && text[at]);
    if(at==limit && text[at]) {
        char line[64];snprintf(line,sizeof line,"PF%u %s truncated at %zu",sequence,field,limit);native_metal_board_text(line);
    }
}
void pipelineFailure(unsigned sequence,const char *stage,const char *error,const char *spec) {
    pipelineFailureField(sequence,"S",stage,32);
    pipelineFailureField(sequence,"E",error,512);
    pipelineFailureField(sequence,"D",spec,384);
}

}  // namespace

extern "C" void native_metal_board_prepare(unsigned port) {
    static bool prepared;
    if (prepared) return;
    prepared = true;
    if (void *iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW | RTLD_LOCAL)) {
        gIOKit.open = (OpenFn)dlsym(iokit, "IOServiceOpen");
        gIOKit.close = (CloseFn)dlsym(iokit, "IOServiceClose");
        gIOKit.call = (CallFn)dlsym(iokit, "IOConnectCallStructMethod");
        gIOKit.map = (MapFn)dlsym(iokit, "IOConnectMapMemory64");
        // The discovery releases the accelerator port when the plugin returns; markers and the hand-off keep using it.
        if (auto retain = (RetainFn)dlsym(iokit, "IOObjectRetain")) retain(port);
    }
    gIOKit.port = port;
    // Qualified GE8300 diagnostic policy; the generic bridge remains unchanged by default.
    const char *fetchPolicy=getenv("PVR_METAL_REQUIRE_FETCH_FOR_APPLE3");
    if(fetchPolicy && !strcmp(fetchPolicy,"1"))
        native_bridge_metal_require_fetch_for_apple3(true);
    const char *pipelineDiag=getenv("PVR_METAL_PIPELINE_DIAG");
    if(pipelineDiag && !strcmp(pipelineDiag,"1")) {
        native_bridge_metal_set_pipeline_failure_callback(pipelineFailure);
        // Actual process main image, not a guessed base of a sampled dylib.
        // One bounded record per preparation; this reads no arbitrary pointers.
        char imageLine[64];
        snprintf(imageLine,sizeof imageLine,"IM main=%llx slide=%llx",
                 (unsigned long long)(uintptr_t)_dyld_get_image_header(0),
                 (unsigned long long)_dyld_get_image_vmaddr_slide(0));
        native_metal_board_text(imageLine);
    }

    // Mesa's PowerVR driver (pvr_darwin.c): the accelerator, not the direct NativePowerVRGpu service, which sandboxed processes may not open.
    setenv("PVR_DARWIN_SERVICE", "IOAcceleratorES", 1);
    char type[16];
    snprintf(type, sizeof type, "0x%x", kMetalOpenType);
    setenv("PVR_DARWIN_OPEN_TYPE", type, 1);
    // The GE8300 (BVNC 22.40.54.30) is not on Mesa's conformant list (pvr_device_is_conformant()).
    setenv("PVR_I_WANT_A_BROKEN_VULKAN_DRIVER", "1", 1);
    // MT8167's terminal PPP clip uses 16-pixel tiles, as the vendor GLES does.
    // Full clear/linear/optimal readback verified by the standalone owner probe.
    setenv("PVR_DARWIN_TERMINATE_TILE_UNITS", "1", 1);
    // Preserve exact caller host ranges; Mesa backs overfetch in separate GPU VA.
    setenv("PVR_DARWIN_BUFFER_GUARD", "1", 1);
    // Use vendor-parity v1 records when Clip is naturally selected. Routing
    // remains upstream's choice unless an external TQ_CLIP experiment is set.
    setenv("PVR_DARWIN_SIPF1", "1", 1);
    unsetenv("PVR_DARWIN_VENDOR_ISP_OVERLAP");
    // The metal2vulkan runtime keeps its own pipeline cache; Mesa's disk cache would write below a home the sandbox may refuse.
    setenv("MESA_SHADER_CACHE_DISABLE", "true", 0);
}

extern "C" void native_metal_board_mark(uint32_t value) {
    unsigned conn = 0;
    value &= 0xffff;
    if (!value || !gIOKit.open || !gIOKit.port) return;
    if (gIOKit.open(gIOKit.port, mach_task_self(), kMarkBase | value, &conn) == 0 && conn && gIOKit.close) gIOKit.close(conn);
}

extern "C" void native_metal_board_text(const char *line) {
    if (!line) return;
    pthread_mutex_lock(&gTextLock);   // the kernel keeps one line per process: a line goes out whole
    native_metal_board_mark(0x7fff);
    for (unsigned i = 0; i < 63 && line[i]; ++i) {
        const uint8_t c = (uint8_t)line[i];
        native_metal_board_mark(0x7000 | (c >= 0x20 && c < 0x7f ? c : '?'));
    }
    native_metal_board_mark(0x7ffd);
    pthread_mutex_unlock(&gTextLock);
}

extern "C" bool native_metal_board_scanout(gpuscanout::Transport *transport) {
    if (!transport || !gIOKit.open || !gIOKit.call || !gIOKit.map || !gIOKit.port) return false;
    if (!gHandoff.conn) {
        unsigned conn = 0;
        const int opened = gIOKit.open(gIOKit.port, mach_task_self(), kHandoffOpenType, &conn);
        if (opened != 0 || !conn) {
            char line[48];
            snprintf(line, sizeof line, "P0 open e%x", (unsigned)opened);
            native_metal_board_text(line);
            return false;
        }
        // The first selector that carries the tunnel (every selector does on a sandbox-granted connection).
        const uint32_t probe[2] = {kTunnelMagic, kTunnelProbe};
        bool found = false;
        for (uint32_t s = 0; s < 256 && !found; ++s) {
            if (gIOKit.call(conn, s, probe, sizeof probe, nullptr, nullptr) == 0) {
                gHandoff.selector = s;
                found = true;
            }
        }
        if (!found) {
            native_metal_board_text("P1 no selector");
            if (gIOKit.close) gIOKit.close(conn);
            return false;
        }
        gHandoff.conn = conn;
    }
    *transport = {&gHandoff, handoffCall, handoffMap, handoffMark};
    return true;
}
