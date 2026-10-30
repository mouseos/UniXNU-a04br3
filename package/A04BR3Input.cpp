// SPDX-License-Identifier: BSD-2-Clause-Patent
// a04br3 touch panel and keys for NativeHidInput (boot argument a04br3-input=1, default off).
//
// Ported from github.com/mouseos/xnu_kernel_benesse_a04br3 bsd/dev/a04br3_touch.c, a04br3_touch_protocol.h and
// a04br3_keys.c (the iOS 16 bring-up of this tablet, where they ran on hardware with calibrated corners), whose register
// facts come from the board Linux BU21025 driver (SANYO TST, 2017), MT8167 i2c-mt65xx, the MT6392 regulator and
// mtk-pmic-wrap tables and a04br3.dts. No Linux code is linked.
//  - Touch: ROHM BU21025 resistive controller at I2C0 (0x11009000) address 0x48, powered from MT6392 VGP1 1.8 V through
//    PWRAP WACS2, polled PIO with the pen interrupt on GPIO8. Each contact is published as one finger to the finger set of
//    package/TouchState.cpp (x 0..30480, y 0..20320 over the 800x1280 portrait panel) and NativeHidInput is notified.
//  - Keys: the MT8167 keypad (0x10002000), bit 0 volume up, bit 1 Home. Both physical down/up states go through
//    NativeHidInput's Consumer report 2 (Home bit 0, volume up bit 2). The mailbox remains for synthetic self-tests only.
//    Needs sheng-touch-hid=1 for the digitizer collection and the touch sink of NativeHidInput.
// The only other PWRAP user, package/A04BR3AutoReset.cpp, finishes its WACS2 transactions in bsd_init before this starts.
#include <IOKit/IOLib.h>
#include <libkern/c++/OSObject.h>
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <mach/mach_time.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include "A04BR3RegisterLock.h"

extern "C" kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
extern "C" void thread_deallocate(thread_t);
extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

extern "C" {
// package/TouchState.cpp
extern volatile uint32_t native_touch_seq, native_touch_count;
extern volatile uint32_t native_touch_hid_reports;
extern volatile int native_hid_input_results[8];
extern volatile int native_touch_xs[12], native_touch_ys[12], native_touch_ids[12];
void native_touch_published(void);
// package/HidInputProbe.cpp: 1 asks NativeHidInput for one Home press and release.
extern volatile uint32_t native_hid_input_command;
void a04br3_input_start(void);
void native_buttons_set_sink(void (*sink)(void *, uint32_t), void *context);
volatile uint32_t a04br3_input_stage, a04br3_touch_contacts, a04br3_key_presses;
volatile int a04br3_touch_error = -1;
}

namespace {

volatile uint8_t *gI2c, *gGpio, *gTop, *gInfra, *gPwrap, *gKeypad;
IOLock *gPublishLock;
// Callback context is the NativeHidInput OSObject. Registration and each in-flight
// notification own a reference; never enter its work loop while holding this lock.
IOLock *gButtonLock;
void (*gButtonSink)(void *, uint32_t);
OSObject *gButtonContext;
uint32_t gButtonMask;
bool gButtonDirty = true;

IOLock *buttonLock()
{
  IOLock *lock = __atomic_load_n(&gButtonLock, __ATOMIC_ACQUIRE);
  if (lock) return lock;
  IOLock *candidate = IOLockAlloc();
  if (!candidate) return nullptr;
  IOLock *expected = nullptr;
  if (!__atomic_compare_exchange_n(&gButtonLock, &expected, candidate, false, __ATOMIC_RELEASE, __ATOMIC_ACQUIRE)) {
    IOLockFree(candidate);
    return expected;
  }
  return candidate;
}

// Called by the single keypad producer, including when a newly registered sink
// needs the current held state. No fabricated press/release or periodic repeat.
void publishButtons(unsigned keypad)
{
  IOLock *lock = buttonLock();
  if (!lock) return;
  const uint32_t mask = ((keypad & 2) ? 1U : 0U) | ((keypad & 1) ? 4U : 0U);
  void (*sink)(void *, uint32_t) = nullptr;
  OSObject *context = nullptr;
  IOLockLock(lock);
  if (mask != gButtonMask) { gButtonMask = mask; gButtonDirty = true; }
  if (gButtonDirty && gButtonSink) {
    sink = gButtonSink; context = gButtonContext;
    context->retain();
    gButtonDirty = false;
  }
  IOLockUnlock(lock);
  if (sink) { sink(context, mask); context->release(); }
}

uint32_t rd(volatile uint8_t *b, unsigned o) { uint32_t v = *reinterpret_cast<volatile uint32_t *>(b + o); __asm__ volatile("dmb oshld" ::: "memory"); return v; }
void wr(volatile uint8_t *b, unsigned o, uint32_t v) { *reinterpret_cast<volatile uint32_t *>(b + o) = v; __asm__ volatile("dsb oshst" ::: "memory"); }
uint16_t ird(unsigned o) { uint16_t v = *reinterpret_cast<volatile uint16_t *>(gI2c + o); __asm__ volatile("dmb oshld" ::: "memory"); return v; }
void iwr(unsigned o, uint16_t v) { *reinterpret_cast<volatile uint16_t *>(gI2c + o) = v; __asm__ volatile("dsb oshst" ::: "memory"); }
uint16_t krd(unsigned o) { uint16_t v = *reinterpret_cast<volatile uint16_t *>(gKeypad + o); __asm__ volatile("dmb oshld" ::: "memory"); return v; }
void kwr(unsigned o, uint16_t v) { *reinterpret_cast<volatile uint16_t *>(gKeypad + o) = v; __asm__ volatile("dsb oshst" ::: "memory"); }

int pwWait(unsigned fsm) { for (unsigned n = 0; n < 1000; n++) { const uint32_t status=rd(gPwrap, 0xa4); if (!(status & (1U<<21))) return -1; if (((status >> 16) & 7) == fsm) return 0; IODelay(10); } return -1; }
int pwRead(unsigned addr, uint16_t *value)
{
  A04BR3PwrapGuard guard(10000);
  if (!guard) return -1;
  if (pwWait(0)) return -1;
  wr(gPwrap, 0xa0, (addr >> 1) << 16);
  if (pwWait(6)) { guard.quarantine(); return -1; }
  *value = uint16_t(rd(gPwrap, 0xa4));
  wr(gPwrap, 0xa8, 1);
  if (pwWait(0)) { guard.quarantine(); return -1; }
  return 0;
}
int pwWrite(unsigned addr, uint16_t value)
{
  A04BR3PwrapGuard guard(10000); if (!guard) return -1;
  if (pwWait(0)) return -1;
  wr(gPwrap, 0xa0, 0x80000000U | ((addr >> 1) << 16) | value);
  // As AutoReset wacs(): a write may finish before the first poll. A bounded run of idle polls (INIT_DONE, no
  // response pending) releases the lock; powerOn() reads the voltage register back.
  bool sawBusy=false;
  unsigned idle=0;
  for (unsigned n=0; n<1000; ++n) {
    const uint32_t status=rd(gPwrap,0xa4);
    if (!(status & (1U<<21))) break;
    if (((status>>16)&7U)!=0) { sawBusy=true; idle=0; }
    else if (sawBusy) return 0;
    else if (++idle >= 100) return 0;
    IODelay(10);
  }
  guard.quarantine(); return -1;
}

// MT6392 VGP1 to 1.8 V (voltage select 0x530 bits 5..7 = 3) and enabled (0x50a bit 15). pwWrite also accepts a write
// that the wrapper ignored, so both written fields are read back.
int powerOn(void)
{
  if (!(rd(gPwrap, 0x9c) & 1)) return -2;
  uint16_t v = 0;
  if (pwRead(0x530, &v) || pwWrite(0x530, uint16_t((v & ~0xe0) | 0x60))) return -3;
  if (pwRead(0x50a, &v) || pwWrite(0x50a, uint16_t(v | 0x8000))) return -4;
  IOSleep(20);
  if (pwRead(0x530, &v)) return -5;
  if ((v & 0xe0) != 0x60) return -6;
  if (pwRead(0x50a, &v)) return -7;
  return (v & 0x8000) ? 0 : -8;
}

int transfer(bool read, uint8_t *data, unsigned len)
{
  if (!len || len > 2) return -1;
  iwr(0x0c, 7); iwr(0x38, 1); iwr(0x08, 0); iwr(0x10, 0x28);   // ACK error and clock extension, PIO
  iwr(0x04, uint16_t((0x48 << 1) | (read ? 1 : 0))); iwr(0x14, uint16_t(len)); iwr(0x18, 1); iwr(0x1c, 2);
  if (!read) for (unsigned i = 0; i < len; i++) iwr(0, data[i]);
  iwr(0x24, 1);
  for (unsigned n = 0; n < 2000; n++) {
    const unsigned status = ird(0x0c);
    if (status & 6) { iwr(0x0c, 7); return -2; }
    if (status & 1) { if (read) for (unsigned i = 0; i < len; i++) data[i] = uint8_t(ird(0)); iwr(0x0c, 7); return 0; }
    IODelay(5);
  }
  iwr(0x50, 1);
  return -3;
}
int command(uint8_t c) { return transfer(false, &c, 1); }
// 12-bit, MSB aligned ADC reply.
int measure(uint8_t set, uint8_t op, unsigned waitUs, unsigned *value)
{
  uint8_t b[2];
  int e = command(set); if (e) return e;
  IODelay(waitUs);
  e = command(op); if (e) return e;
  e = transfer(true, b, 2);
  if (!e) *value = (unsigned(b[0]) << 4) | (b[1] >> 4);
  return e;
}
// 1 contact, 0 release, -1 invalid or noisy sample (keep the previous state). Corner bounds measured on this tablet
// (2026-10-21, two passes); the landscape horizontal axis is portrait Y and inverted.
int point(unsigned x, unsigned y, unsigned z, unsigned *px, unsigned *py)
{
  if (x > 4095 || y > 4095 || z > 4095) return -1;
  if (z <= 5 || z == 4095) return 0;
  const int64_t resistance = int64_t((730 * x) / 4096) * ((4096000 / z) - 1000) - int64_t(1000 - (y * 1000) / 4096) * 305;
  if (resistance > 900000) return -1;
  int cx = int(x) - 150, cy = int(y) - 320;
  if (cx < 0) cx = 0; else if (cx > 3800) cx = 3800;
  if (cy < 0) cy = 0; else if (cy > 3510) cy = 3510;
  *px = unsigned(((3510 - cy) * 799) / 3510);
  *py = unsigned(1279 - (cx * 1279) / 3800);
  return 1;
}

void publish(bool down, unsigned px, unsigned py)
{
  // Both the panel and the optional self-test publish here. A sequence lock
  // protects readers only: writers must exclude one another or their increments
  // can be lost, leaving an odd sequence that touchSend rejects indefinitely.
  IOLockLock(gPublishLock);
  native_touch_seq = native_touch_seq + 1;   // odd: writing
  __asm__ volatile("dmb ish" ::: "memory");
  native_touch_count = down ? 1 : 0;
  native_touch_ids[0] = 0;
  native_touch_xs[0] = int((px * 30479U) / 799U);
  native_touch_ys[0] = int((py * 20319U) / 1279U);
  __asm__ volatile("dmb ish" ::: "memory");
  native_touch_seq = native_touch_seq + 1;   // even: published
  IOLockUnlock(gPublishLock);
  // The sink enters NativeHidInput's work loop. Never hold the writer lock
  // across this synchronous callback.
  native_touch_published();
}

void touchThread(void *, wait_result_t)
{
  int e = powerOn();
  if (e) { a04br3_touch_error = e; IOLog("a04br3 input: touch VGP1 1.8 V failed (%d)\n", e); return; }
  // GPIO58/59 ALT1 (I2C0), GPIO8 input (pen interrupt); only these fields change.
  for (unsigned pin = 58; pin <= 59; pin++) {
    const unsigned off = 0x300 + (pin / 5) * 0x10, shift = (pin % 5) * 3;
    wr(gGpio, off, (rd(gGpio, off) & ~(7U << shift)) | (1U << shift));
  }
  wr(gGpio, 0x310, rd(gGpio, 0x310) & ~(7U << 9));
  wr(gGpio, 0x08, 1U << 8);
  wr(gGpio, 0x904, (1U << 3) | (1U << 15));
  wr(gTop, 0x84, 1U << 3);
  if (!a04br3_infra_update(gInfra, 1U << 1, 0)) {
    a04br3_touch_error = -1; IOLog("a04br3 input: shared infra mux unavailable\n"); return;
  }
  iwr(0x50, 1); iwr(0x40, 3); iwr(0x54, 0); iwr(0x70, 4); iwr(0x20, 0x73f); iwr(0x48, 0); iwr(0x28, 0x8001);
  e = command(0x50); if (!e) e = command(0xb0); if (!e) e = command(0);
  a04br3_touch_error = e;
  IOLog("a04br3 input: BU21025 i2c0@48 init %d\n", e);
  if (e) return;
  a04br3_input_stage = a04br3_input_stage | 1;
  unsigned lastx = 0, lasty = 0, failures = 0, perf = 0, perfEdges = 0;
  PE_parse_boot_argn("a04br3-home-perf", &perf, sizeof(perf));
  bool down = false;
  for (;;) {
    if (!down && (rd(gGpio, 0x200) & (1U << 8))) { IOSleep(20); continue; }   // pen up
    unsigned x = 0, y = 0, z = 0;
    e = measure(0x80, 0xc4, 1500, &x);
    if (!e) e = measure(0x90, 0xd4, 800, &y);
    if (!e) e = measure(0xa0, 0xe4, 600, &z);
    if (e) {
      if (down) {
        uint64_t before = 0;
        if (perf == 1 && perfEdges < 32) before = mach_absolute_time();
        publish(false, lastx, lasty); down = false;
        if (perf == 1 && perfEdges < 32) {
          const uint64_t after = mach_absolute_time();
          uint64_t startNs = 0, elapsedNs = 0;
          absolutetime_to_nanoseconds(before, &startNs);
          absolutetime_to_nanoseconds(after - before, &elapsedNs);
          IOLog("a04br3 perf input edge=0 error=%d seq=%u start_ns=%llu publish_ns=%llu reports=%u\n",
                e, native_touch_seq, startNs, elapsedNs, native_touch_hid_reports);
          ++perfEdges;
        }
      }
      if (++failures <= 3) IOLog("a04br3 input: touch transfer error %d\n", e);
      IOSleep(100);
      continue;
    }
    failures = 0;
    const int active = point(x, y, z, &lastx, &lasty);
    if (active < 0) { (void)command(0); IOSleep(20); continue; }
    bool edge = (active != 0) != down;
    uint64_t before = 0, after = 0;
    if (perf == 1 && edge && perfEdges < 32) before = mach_absolute_time();
    if (active || down) publish(active != 0, lastx, lasty);
    if (perf == 1 && edge && perfEdges < 32) {
      after = mach_absolute_time(); uint64_t beforeNs = 0, elapsedNs = 0;
      absolutetime_to_nanoseconds(before, &beforeNs);
      absolutetime_to_nanoseconds(after - before, &elapsedNs);
      IOLog("a04br3 perf input edge=%u seq=%u start_ns=%llu publish_ns=%llu reports=%u\n",
            active != 0, native_touch_seq, beforeNs, elapsedNs, native_touch_hid_reports);
      ++perfEdges;
    }
    if ((active != 0) != down) {
      if (active) a04br3_touch_contacts = a04br3_touch_contacts + 1;
      if (a04br3_touch_contacts <= 32) IOLog("a04br3 input: touch %s %u,%u seq %u reports %u result 0x%x\n",
          active ? "down" : "up", lastx, lasty, native_touch_seq, native_touch_hid_reports,
          unsigned(native_hid_input_results[6]));
    }
    down = active != 0;
    (void)command(0);
    IOSleep(20);
  }
}

void keyThread(void *, wait_result_t)
{
  kwr(0x18, 1024); kwr(0x24, 1);   // as LK leaves the scan: debounce and enable
  a04br3_input_stage = a04br3_input_stage | 2;
  unsigned previous = 0, candidate = 0, stable = 0;
  for (;;) {
    const unsigned value = (~krd(4)) & 3;
    if (value == candidate) stable++; else { candidate = value; stable = 0; }
    if (stable >= 2 && candidate != previous) {
      const unsigned changed = candidate ^ previous;
      if ((changed & 2) && (candidate & 2)) a04br3_key_presses = a04br3_key_presses + 1;
      IOLog("a04br3 input: keys volup %u home %u\n", candidate & 1, (candidate >> 1) & 1);
      previous = candidate;
    }
    publishButtons(previous);
    IOSleep(10);
  }
}

// a04br3-input-selftest=<seconds> (bring-up, default off): <seconds> after the input start, one synthetic rightward swipe
// across the middle of the panel (41 points 20 ms apart) through the same finger set and notification as the panel, so
// the touch path to iPadOS is tested without anyone at the tablet: on the first home screen page it opens the widget view
// (a page with no previous page would not change on a leftward swipe). NativeHidInput's touch report count and the result of
// its last report go to UART before and after. The panel's own readings are not involved.
extern "C" {
extern volatile uint32_t native_hid_input_events[8];
extern volatile uint32_t native_touch_edge_stats[4];
extern char native_hid_input_props[1024];
}
unsigned gSelftestSeconds;
void selftestThread(void *, wait_result_t)
{
  IOSleep(gSelftestSeconds * 1000);
  // Wake the display first: a sleeping or locked home screen ignores touches (Home through NativeHidInput, then 5 s).
  native_hid_input_command = 1;
  IOSleep(5000);
  IOLog("a04br3 input: selftest rightward swipe, touch reports %u result 0x%x\n", native_touch_hid_reports,
        unsigned(native_hid_input_results[6]));
  IOLog("a04br3 input: event service %.200s\n", native_hid_input_props);
  for (unsigned i = 0; i <= 40; i++) {
    publish(true, 100 + i * 15, 640);
    IOSleep(20);
  }
  publish(false, 700, 640);
  IOSleep(1000);
  IOLog("a04br3 input: selftest done, touch reports %u result 0x%x events %u/%u/%u/%u/%u/%u/%u/%u edge %u/%u/%u/%u\n",
        native_touch_hid_reports, unsigned(native_hid_input_results[6]), native_hid_input_events[0], native_hid_input_events[1],
        native_hid_input_events[2], native_hid_input_events[3], native_hid_input_events[4], native_hid_input_events[5],
        native_hid_input_events[6], native_hid_input_events[7], native_touch_edge_stats[0], native_touch_edge_stats[1],
        native_touch_edge_stats[2], native_touch_edge_stats[3]);
}

void start(void (*fn)(void *, wait_result_t))
{
  thread_t thread = nullptr;
  if (kernel_thread_start(fn, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

}  // namespace

// Only this board opts into physical Home in the existing Consumer report.
extern "C" bool native_buttons_physical_home(void) { return true; }

// NativeHidInput supplies its OSObject as context and unregisters before stop.
// Replacing/removing a sink releases registration ownership outside the lock;
// an already dispatched notification retains its own context until it returns.
void native_buttons_set_sink(void (*sink)(void *, uint32_t), void *context)
{
  IOLock *lock = buttonLock();
  if (!lock) return;
  OSObject *next = sink ? static_cast<OSObject *>(context) : nullptr;
  if (sink && !next) return;
  if (next) next->retain();
  IOLockLock(lock);
  OSObject *old = gButtonContext;
  gButtonSink = sink; gButtonContext = next; gButtonDirty = true;
  IOLockUnlock(lock);
  if (old) old->release();
}

void a04br3_input_start(void)
{
  unsigned enabled = 0;
  if (!PE_parse_boot_argn("a04br3-input", &enabled, sizeof(enabled)) || enabled != 1) return;
  gI2c = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x11009000, 0x1000));
  gGpio = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10005000, 0x1000));
  gTop = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10000000, 0x1000));
  gInfra = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10001000, 0x1000));
  gPwrap = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x1000F000, 0x1000));
  gKeypad = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10002000, 0x1000));
  if (!gI2c || !gGpio || !gTop || !gInfra || !gPwrap || !gKeypad) { IOLog("a04br3 input: register mapping failed\n"); return; }
  gPublishLock = IOLockAlloc();
  if (!gPublishLock) { IOLog("a04br3 input: publisher lock allocation failed\n"); return; }
  start(touchThread);
  start(keyThread);
  if (PE_parse_boot_argn("a04br3-input-selftest", &gSelftestSeconds, sizeof(gSelftestSeconds)) && gSelftestSeconds) start(selftestThread);
}
