/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Included by the genuine IONetworkInterface implementation, where its private
 * backing field and original configuration bits are available. Not a new TU.
 */
IOReturn IONetworkInterface::adoptExistingIfnet(ifnet_t interface)
{
    if(!interface || !_privateLock)return kIOReturnBadArgument;
    IOLockLock(_privateLock);
    if(_backingIfnet){IOLockUnlock(_privateLock);return kIOReturnBusy;}
    // External bridge supplies legacy callbacks/softc, not Apple's pull/poll
    // callbacks. Reject a configuration that would reinterpret that softc.
    if(_configFlags & (kConfigRxPoll | kConfigTxPull)){
        IOLockUnlock(_privateLock);return kIOReturnUnsupported;
    }
    errno_t error=ifnet_reference(interface);
    if(!error){_backingIfnet=interface;_configFlags|=kConfigFrozen;}
    IOLockUnlock(_privateLock);
    return error?kIOReturnError:kIOReturnSuccess;
}

IOReturn IONetworkInterface::clearExistingIfnet(ifnet_t expected)
{
    if(!expected || !_privateLock)return kIOReturnBadArgument;
    IOLockLock(_privateLock);
    if(_backingIfnet!=expected){IOLockUnlock(_privateLock);return kIOReturnBadArgument;}
    _backingIfnet=nullptr; // base free() must not release this reference again
    _flags &= ~(IFF_UP | IFF_RUNNING);
    IOLockUnlock(_privateLock);
    ifnet_release(expected); // only after the real external detach completed
    return kIOReturnSuccess;
}
