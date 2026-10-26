/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * A04BR3 iOS16 caller compatibility. The shipped configd signature grants
 * IONetworkStackUserClient in com.apple.security.iokit-user-client-class;
 * the newer pinned family instead requires its dedicated boolean entitlement.
 * Only the actual task's signed entitlements may reach this predicate. The
 * caller must first pass IOUserClient init and administrator checks. This is
 * an explicit class grant, never a root-only or process-name exception.
 */
#ifndef A04BR3_STACK_AUTHORIZATION_H
#define A04BR3_STACK_AUTHORIZATION_H
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSString.h>

static inline bool a04_stack_class_authorized(OSObject *entitlement)
{
    OSArray *classes = OSDynamicCast(OSArray, entitlement);
    if (!classes || !classes->getCount()) return false;
    bool granted = false;
    for (unsigned i = 0; i < classes->getCount(); ++i) {
        OSString *name = OSDynamicCast(OSString, classes->getObject(i));
        if (!name) return false; // malformed signed policy is not a grant
        if (name->isEqualTo("IONetworkStackUserClient")) granted = true;
    }
    return granted;
}
#endif
