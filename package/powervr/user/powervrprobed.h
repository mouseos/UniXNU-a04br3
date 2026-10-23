/* SPDX-License-Identifier: MIT */
/* powervrprobed: the user-space test of the NativePowerVR user client (package/powervr/NativePowerVRGpu.cpp). Shared
 * declarations of powervrprobed.c (the IOKit stage) and powervrprobed_vulkan.c (the optional Mesa stage). */
#ifndef POWERVRPROBED_H
#define POWERVRPROBED_H

#include <stdint.h>

/* One UART line: "powervrprobed: " + the text, written with a single write() to /dev/console (see powervrprobed.c). */
void probe_line(const char *format, ...) __attribute__((format(printf, 1, 2)));

/* Step accounting of the summary line. */
void probe_pass(void);
void probe_fail(void);

/* The Mesa stage: vkCreateInstance .. vkCreateDevice, a cleared image read back through a buffer. Returns 0 when the read
 * back matches, a negative number otherwise. Built only with POWERVRPROBED_VULKAN=1 (powervrprobed_vulkan.c). */
int probe_vulkan(const char *library);

#endif
