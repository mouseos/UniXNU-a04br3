/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * In-tree build adaptation, not an SDK/header or platform API stub.
 * The pinned family uses only TARGET_OS_IPHONE. XNU kernel builds need not
 * install the user-SDK TargetConditionals.h. Preserve an explicit target macro;
 * otherwise derive it from the actual board/XNU build selection.
 */
#ifndef A04BR3_FAMILY_TARGET_H
#define A04BR3_FAMILY_TARGET_H
#ifndef TARGET_OS_IPHONE
#if defined(ARM64_BOARD_CONFIG_A04BR3)
/* This source board boots the iOS runtime despite the compatibility kernel's
 * XNU_TARGET_OS_OSX build mode; do not select the macOS network-root behavior. */
#define TARGET_OS_IPHONE 1
#elif defined(XNU_TARGET_OS_IOS) || defined(XNU_TARGET_OS_TV) || defined(XNU_TARGET_OS_WATCH) || defined(XNU_TARGET_OS_BRIDGE)
#define TARGET_OS_IPHONE (XNU_TARGET_OS_IOS || XNU_TARGET_OS_TV || XNU_TARGET_OS_WATCH || XNU_TARGET_OS_BRIDGE)
#elif defined(XNU_TARGET_OS_OSX)
#define TARGET_OS_IPHONE 0
#else
#error "IONetworkingFamily requires a known board/XNU target selection"
#endif
#endif
#endif
