// SPDX-License-Identifier: MIT
// The registry-visible accelerator of the PowerVR GE8300 for the original Metal discovery (IOServiceMatching("IOAcceleratorES") ->
// MetalPluginName -> /System/Library/Extensions/<name>.bundle). Copied from UniXNU drivers/NativeAdreno/NativeAdrenoAccelerator.cpp with
// the Adreno user-client factory replaced by the PowerVR one (NativePowerVRMetal.cpp); nothing else differs. It derives from the original
// IOAcceleratorES base as qualified in UniXNU drivers/NativeMetal/OriginalAcceleratorABI.h (no operational overrides), so the only
// virtuals it adds are newUserClient and the property reads (counted for the console). Its static initializer is staged out of
// __mod_init_func (UniXNU scripts/build-native-adreno-accelerator.py --source ... --into ...) and run by NativePowerVRMetal.cpp after
// the original IOGPUFamily kext has loaded, because the parent metaclass does not exist before that. The class keeps the Apple
// accelerator's name, as the Adreno one does, so original sandbox profiles that name the service class accept the plugin's open;
// no Apple GPU kext is loaded on this board.
#include "OriginalAcceleratorABI.h"
#include <IOKit/IOUserClient.h>

extern "C" IOReturn native_powervr_metal_new_user_client(IOService *, task_t, void *, UInt32, OSDictionary *, IOUserClient **);
extern "C" void native_powervr_metal_note_property_read(const char *);

class AGXAccelerator : public IOAcceleratorES {
    OSDeclareDefaultStructors(AGXAccelerator)
public:
    IOReturn newUserClient(task_t owningTask,void *securityID,UInt32 type,OSDictionary *properties,
        IOUserClient **handler) APPLE_KEXT_OVERRIDE;
    OSObject *copyProperty(const OSSymbol *key) const APPLE_KEXT_OVERRIDE;
    OSObject *copyProperty(const OSString *key) const APPLE_KEXT_OVERRIDE;
    OSObject *copyProperty(const char *key) const APPLE_KEXT_OVERRIDE;
};
OSDefineMetaClassAndStructors(AGXAccelerator,IOAcceleratorES)

IOReturn AGXAccelerator::newUserClient(task_t owningTask,void *securityID,UInt32 type,OSDictionary *properties,
    IOUserClient **handler)
{
    return native_powervr_metal_new_user_client(this,owningTask,securityID,type,properties,handler);
}
OSObject *AGXAccelerator::copyProperty(const OSSymbol *key) const
{
    native_powervr_metal_note_property_read(key?key->getCStringNoCopy():nullptr);
    return IORegistryEntry::copyProperty(key);
}
OSObject *AGXAccelerator::copyProperty(const OSString *key) const
{
    native_powervr_metal_note_property_read(key?key->getCStringNoCopy():nullptr);
    return IORegistryEntry::copyProperty(key);
}
OSObject *AGXAccelerator::copyProperty(const char *key) const
{
    native_powervr_metal_note_property_read(key);
    return IORegistryEntry::copyProperty(key);
}
extern "C" IOService *native_powervr_accelerator_allocate(void) {return new AGXAccelerator;}
extern "C" unsigned native_powervr_accelerator_instance_size(void) {return sizeof(AGXAccelerator);}
