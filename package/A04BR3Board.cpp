// SPDX-License-Identifier: MIT
// a04br3 board hooks of the kernel, linked from libA04BR3Board.a (CONFIG_XNU_EXTRA_LD_ARCHIVES, scripts/build-a04br3-board.py).
//
// unixnu_board_init(): bsd_init, before the root mount (CONFIG_XNU_BOARD_INIT=y, UniXNU patch native-board-init-hook).
//  - the unattended test cycle (package/A04BR3AutoReset.cpp, a04br3-autoreset=<seconds>);
//  - the AppleKeyStore stand-in (package/KeyStoreProbe.cpp + UniXNU drivers/NativeKeyStoreEvents, offline-key-events=1 and
//    sheng-aks=1). UniXNU-tb128fu and UniXNU-sheng call native_keystore_probe() from native_legacy_display_late_probe()
//    (LegacyDisplayProbe.cpp). The a04br3 copy of that probe leaves it out and the board hook calls it: it runs with or
//    without CONFIG_XNU_NATIVE_LEGACY_DISPLAY, and the key store objects are only in the board archive. Without it
//    liveactivitiesd's keybag finds no AppleKeyStore and SpringBoard waits on chronod forever (UniXNU-tb128fu board.toml).
//
// unixnu_board_launch(): load_init_program, after the root, SystemOS and Data mounts and before launchd
// (CONFIG_XNU_BOARD_LAUNCH=y, proposed UniXNU patch upstream/UniXNU/0001-native-board-launch-hook.patch). It writes the
// launchd disabled-jobs policy of sheng-disable-watchdogd / sheng-disable-jobs (package/BootWatchdogPolicy.c). In UniXNU the
// only caller of that policy is zzz-boot-progress.patch under XNU_BOOT_PROGRESS, which needs CONFIG_XNU_NATIVE_UFS. Until
// the hook is in UniXNU, scripts/a04br3-seed-launch-policy.py writes the same file into the Data seed at build time.
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include <kern/thread.h>
#include <libkern/libkern.h>

extern "C" {
void a04br3_autoreset_init(void);
void native_keystore_probe(void);
int sheng_boot_watchdog_policy(void *process, uint64_t scratch);
extern volatile uint32_t native_keyevents_stage;
extern volatile int native_keyevents_status;
// KeyStoreProbe.cpp writes its counters into two rows of the boot-progress page (CONFIG_XNU_BOOT_PROGRESS), which this board
// does not link: the rows go to this zero-initialised storage instead. Weak, so a real page linked later takes precedence.
__attribute__((weak)) char boot_progress_ext[8][48];
}

// a04br3-proclog=<seconds>: every <seconds>, one UART line per batch of processes ("a04br3 procs: pid:name ..."). Without a
// display this is the only view of userland progress after launchd; off by default.
extern "C" {
typedef struct proc *proc_t;
typedef int (*proc_iterate_fn_t)(proc_t, void *);
void proc_iterate(unsigned int flags, proc_iterate_fn_t callout, void *arg, proc_iterate_fn_t filterfn, void *filterarg);
int proc_pid(proc_t);
void proc_name(int pid, char *buf, int size);
}

// The display state on UART with each process list (package/LegacyDisplayProbe.cpp; weak, so a board image without the
// display archive still links).
extern "C" {
extern volatile uint32_t native_legacy_stage __attribute__((weak));
extern volatile int native_legacy_meta_status __attribute__((weak));
extern volatile uint32_t native_legacy_start_result __attribute__((weak));
extern volatile uint64_t native_legacy_gop_physical __attribute__((weak)), native_legacy_gop_bytes __attribute__((weak));
extern volatile uint32_t native_legacy_gop_width __attribute__((weak)), native_legacy_gop_height __attribute__((weak));
extern volatile int native_legacy_surface_status __attribute__((weak)), native_legacy_pm_status __attribute__((weak));
extern volatile int native_legacy_client_pid __attribute__((weak)), native_legacy_transfer_status __attribute__((weak));
extern volatile uint32_t native_legacy_client_calls __attribute__((weak)), native_legacy_swap_validation_calls __attribute__((weak));
extern volatile uint32_t native_legacy_swap_validation_reason __attribute__((weak)), native_legacy_transfer_calls __attribute__((weak));
extern volatile uint32_t native_legacy_presented_generation __attribute__((weak)), native_legacy_retired_frames __attribute__((weak));
extern volatile uint32_t native_legacy_registration_calls __attribute__((weak));
unsigned native_legacy_prepare_report(char *out, unsigned cap) __attribute__((weak));
}

static void a04br3_display_report(void)
{
  if (!&native_legacy_stage) return;
  IOLog("a04br3 display: stage %u meta %d start %u gop %llx+%llx %ux%u reg %u surface %d pm %d client pid %d calls %u "
        "swaps %u reason %u transfers %u status %d presented %u retired %u\n",
        native_legacy_stage, native_legacy_meta_status, native_legacy_start_result,
        (unsigned long long)native_legacy_gop_physical, (unsigned long long)native_legacy_gop_bytes, native_legacy_gop_width,
        native_legacy_gop_height, native_legacy_registration_calls, native_legacy_surface_status, native_legacy_pm_status,
        native_legacy_client_pid, native_legacy_client_calls, native_legacy_swap_validation_calls,
        native_legacy_swap_validation_reason, native_legacy_transfer_calls, native_legacy_transfer_status,
        native_legacy_presented_generation, native_legacy_retired_frames);
  if (&native_legacy_prepare_report) {
    static char text[400];
    native_legacy_prepare_report(text, sizeof(text));
    IOLog("a04br3 display: %s\n", text);
  }
}

namespace {
constexpr unsigned kProcAll = 0x1;       // PROC_ALLPROCLIST
constexpr int kProcReturned = 0;         // PROC_RETURNED
struct ProcLine { char text[480]; unsigned used; unsigned count; };
unsigned gProcLogSeconds;

void procFlush(ProcLine &line)
{
  if (line.used) IOLog("a04br3 procs:%s\n", line.text);
  line.used = 0;
  line.text[0] = 0;
}

int procCallout(proc_t p, void *arg)
{
  ProcLine &line = *static_cast<ProcLine *>(arg);
  char name[32] = {};
  int pid = proc_pid(p);
  proc_name(pid, name, sizeof(name));
  char entry[48];
  int n = snprintf(entry, sizeof(entry), " %d:%s", pid, name);
  if (n <= 0) return kProcReturned;
  if (line.used + unsigned(n) >= sizeof(line.text)) procFlush(line);
  strlcpy(line.text + line.used, entry, sizeof(line.text) - line.used);
  line.used += unsigned(n) < sizeof(entry) ? unsigned(n) : sizeof(entry) - 1;
  line.count++;
  return kProcReturned;
}

void procLogThread(void *, wait_result_t)
{
  for (unsigned round = 1;; ++round) {
    IOSleep(gProcLogSeconds * 1000);
    static ProcLine line;
    line = {};
    proc_iterate(kProcAll, procCallout, &line, nullptr, nullptr);
    procFlush(line);
    IOLog("a04br3 procs: round %u, %u processes\n", round, line.count);
    a04br3_display_report();
  }
}
}

static void a04br3_proclog_init(void)
{
  if (!PE_parse_boot_argn("a04br3-proclog", &gProcLogSeconds, sizeof(gProcLogSeconds)) || !gProcLogSeconds) return;
  thread_t thread;
  if (kernel_thread_start(procLogThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

extern "C" void unixnu_board_init(void)
{
  a04br3_autoreset_init();
  a04br3_proclog_init();
  unsigned events = 0;
  if (PE_parse_boot_argn("offline-key-events", &events, sizeof(events)) && events == 1) {
    native_keystore_probe();
    IOLog("a04br3: AppleKeyStore stand-in stage %u status 0x%x\n", native_keyevents_stage, (unsigned)native_keyevents_status);
  }
}

extern "C" int unixnu_board_launch(void *process, uint64_t scratch)
{
  return sheng_boot_watchdog_policy(process, scratch);
}
