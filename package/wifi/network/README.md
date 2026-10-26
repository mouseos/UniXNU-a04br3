# A04BR3 BSD network adapter

This original component creates a real Ethernet-family DLIL ifnet with WiFi
subfamily and the hardware MAC supplied by a real backend. Its source-owned
IO80211Controller and IO80211Interface implement the bounded native BSD ioctl
path; they do not implement Apple private C++ vtables or IO80211 user clients. No built-in scan records, association success,
MAC address, firmware-ready defaults, or Apple user client are provided.
`tests/` alone supplies mock backend state/results.

## Owner integration

Add `iokit/Kernel/arm/network/A04BR3BSDNetwork.cpp optional iokitcpp` to the
kernel's files.arm64 in the integrating change. Existing configuration and SDIO
sources are intentionally outside this component's edit scope. The real WLAN
owner creates and attaches an IOService child under the SDIO provider, then calls
`attach(child, &backend, unit)` after BSD Ethernet plumbing is available. Use an
available unit; attach errors must be handled, not treated as ready. The bridge
retains the child. Do not load proprietary IO80211Family alongside the source
classes: duplicate metaclasses are incompatible. No Apple IO80211 user client
is provided; native callers must use the BSD ioctl fallback. State, callback context and the bridge object
must remain alive until detach completion. Backend callbacks run under the
bridge lock and must not reenter it; transmit must queue promptly.

`firmware_running` refers to actual WLAN main firmware, not WMT download ACKs.
`control_ready` is the real WLAN command transport. `set_admin(1)` enables radio
control before scan and association. IFF_UP requires control readiness only,
never association or transmit support. Native GET likewise requires observed
control readiness, independently of admin UP and association. A successful
radio command is not a connection result. State is sampled again after that
command. RUNNING is gated by admin UP, real association, real datapath and
transmit callback; it clears on DOWN, disconnect, readiness loss or detach.
XNU defines IFF_RUNNING as resources allocated, so this stronger local policy
must not be mistaken for an independently verified link event or IP reachability.
Call `refresh()` on every real backend state transition. Link event delivery,
RX handoff and private scan/control payload codecs still need integration with
the actual backend; this component alone does not make WiFi usable.

`native_get` receives the observed ARM64 native GET envelope (40-byte ioctl
0xc02869c9, pointer at offset 32), with bounded copyin/copyout scratch. It must
return only real operation results and EOPNOTSUPP for unsupported requests.
Native SET uses the same envelope and ioctl 0x802869c8 from retained REA
SetWrapper evidence. No guessed scan opcode is implemented. Maximum payload is 16384;
32-bit envelopes are rejected. SET is bounded input only (zero to 16384 bytes),
never copies data back or changes request length. A zero-length payload is passed
as NULL/0 without inferring its operation meaning. Missing native_set returns
EOPNOTSUPP. The appended callback receives opaque type/value and a kernel-owned
input copy valid for the call only. Any asynchronous command must retain its own
copy. Unknown operations return EOPNOTSUPP; a successful SET alone does not set
association or RUNNING. State transitions still require a real refresh().

SET authorization is deliberately root-only via the active calling thread's
kauth_cred_get()/kauth_cred_issuser(), before user copyin or backend dispatch.
The generic BSD ioctl fallback does not authorize this private native command.
No request-provided UID, cached service credential, entitlement bypass, or root
impersonation is used. This is a conservative bridge policy, not a reconstruction
of Apple's entitlement policy; non-root entitled clients are not supported yet.
GET keeps its existing read-only policy. Admin flags retain BSD's existing
SIOCSIFFLAGS authorization. Include sys/kauth.h and existing BSD kauth providers
in real SDK compile/link validation. No backend result is copied on failure.

On shutdown call `detach()`, wait until `isDetached()`, then `finish()` before
releasing owner/context. Successful detach may complete inline or asynchronously.
A failed detach preserves the live ifnet, restores discovery identity properties,
recomputes readiness from the backend, and leaves admin UP/RUNNING cleared. The
caller can retry detach or explicitly enable UP again. `finish()` returns EBUSY
while a live ifnet remains. Serialize owner attach/refresh/detach/finish calls;
finish must not race an active call. Properties are published only after actual
ifnet attach: IOInterfaceName/Prefix/Unit and role Infrastructure. Native wifid
class matching, parent matching, SC dynamic store delivery and IOServiceOpen
fallback reachability remain unverified on device.

## Validation and sources

Run `python3 tests/test_bsd_network.py`. It compiles the actual C++ bridge against
typed DLIL/IOKit mocks with ASan/UBSan, checks ownership and bounded ioctl copies,
including SET privilege denial/no-copy, exact opaque dispatch, zero/max input,
copy faults and backend errors; UP while unassociated, GET before association, no premature RUNNING,
admin failures, state loss, missing TX, inline/async detach and detach failure
recovery. It does not compile against the complete kernel or run native wifid.

SOURCES.json records official Apple API/reference provenance and file hashes.
No Apple reference implementation was copied into this directory. Retained REA
observations and reference downloads are under the worker log directory rather
than this source repository. The native operation backend must implement its
own proven payload codecs; an opaque passthrough is not proof of native binding.

## Provider wiring and native publication prerequisites

`attachProvider(provider, &backend, unit)` creates/initializes/attaches a real
IOService child, then registers its real BSD ifnet. It unwinds failed init,
provider attach and BSD attach without leaking the child. `registryEntry()` is
a borrowed pointer for integrator observation, not an ownership transfer.
`finish()` detaches/releases an internally created child only after DLIL detach
completion. Existing `attach(actualInterfaceService,...)` remains available for
integration with a genuine IONetworkingFamily service; that owner manages its
own provider attachment.

Published after BSD attach: IOInterfaceName, IOBSDName, prefix, unit,
IOInterfaceType=IFT_ETHER, IOMACAddress (actual backend MAC), Infrastructure role.
Detach removes these properties; failed detach restores them with admin DOWN.
No synthetic IOBuiltin, private IOClass, country, regulatory map or calibration
is invented. Supply MAC from real runtime a04br3_wlan_read_calibration(bytes,512); firmware
ready is real WLAN state, never merely WMT delivery success.

`input(mbuf)` consumes a real RX packet on every return. The backend must first
construct an Ethernet mbuf in native DLIL input format (packet-header frame
pointer and payload position for ether_demux), not pass firmware descriptors.
The bridge gates RX on admin plus actual data/association state, retains ifnet
while crossing DLIL, and releases its lock before ifnet_input to permit protocol
reentry. It never frees an mbuf twice after DLIL failure. Owner calls must remain
serialized with final finish; no interrupt invocation of this sleepable gate.

IMPORTANT: A real BSD ifnet plus a plain IOService child is insufficient evidence
of native WiFi discovery. Official configd reference commit
585b7f2fca293f4642d21d15c5daf187f63c4796 checks real IONetworkInterface ancestry
in SCNetworkInterface.c:7646 and registers InterfaceNamer notifications for that
class (ifnamer.c:4421). It also classifies Ethernet as WiFi via actual parent
IO80211Controller/AirPort ancestry (SCNetworkInterface.c:1792). This is a public
reference prerequisite. REA analysis of the shipped iOS16 SystemConfiguration
processNetworkInterface confirms the IO80211Controller ancestry classification. This bridge does NOT spoof class properties,
metaclass ancestry, SC dynamic-store Interfaces, or a WiFi-connected flag.
The source-owned wireless controller and interface provide this hierarchy,
but native WiFi UI visibility still requires hardware validation.

## Genuine ancestry integration candidate

IO80211Interface inherits the vendored genuine IOEthernetInterface and
IONetworkInterface; IO80211Controller inherits IOEthernetController. The old
A04BR3Ethernet type names are source aliases. These functional subclasses own
real lifecycle, MAC, radio, packet filter and native ioctl dispatch, with no
claim to the proprietary Apple class layout or user-client ABI.
The documented virtual DDL attach/detach hooks use the existing bridge as the
single ifnet owner. Apple's default ifnet allocator is not called for this
subclass. getIfnet/getFlags report the actual bridge ifnet. IONetworkStack owns
normal unit/name assignment and registered-state changes. Detach sleeps until
the real asynchronous callback completes; no polling or fake completion.

`a04br3_network_start(provider,&backend,&errno)` in A04BR3NetworkRuntime.h creates
the real Apple IONetworkStack service (one boot-lifetime instance) and the real
controller/interface hierarchy. Call from serialized board startup after BSD
and runtime NVRAM validation. The provider/context live for the boot. Controller
stop synchronously detaches its interface. Backend receives the actual MAC from
`a04br3_wlan_read_calibration(bytes,512)`: GPTnvram dynamic bitmap/map/checksum
reads under the existing MSDC mutex, no extraction or configuration by users.
No calibration file or per-user build input. Distribution uses prebuilt images.

Compile network's three original cpp files and twelve Apple cpp files via the
network-only additions in iokit/conf/files.arm64. Headers use source-local quoted
includes; no runner-only include path or KDK network headers. apple/SOURCE.json
records original and adapted hashes and APPLE_LICENSE is included. Tests:
`python3 tests/test_bsd_network.py`; `python3 tests/test_ethernet_binding.py`.
The adapter test uses typed Apple/DLIL mocks to execute actual adapter/bridge
lifecycle plus verifies vendored-source hashes. It is not a full Apple family
compile or native UI success claim. SDK compile/link and hardware tests are the
parent's next gate. Native iOS WiFi UI validation and remaining operation payloads remain
independent evidence requirements; no AirPort bool/property or class spoof is used.

The current runtime GET subset is power (19), own driver version (43), and
optional capability mask (12). Native runtime SET and scan codecs still return
unsupported. Class registration and successful GET do not establish working
scan, association, WPA2 or connectivity.
