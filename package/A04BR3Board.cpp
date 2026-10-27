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
// After the policy it starts the PowerVR user-space probe once under a04br3-powervrprobed=1 (package/A04BR3ProbeLaunch.c);
// that start has no build-time stand-in and waits for the hook.
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include "BootMemoryQueueSnapshot.h"
#include "powervr/native_powervr_bo_stats.h"
#include <kern/thread.h>
#include <libkern/libkern.h>

extern "C" {
void a04br3_autoreset_init(void);
void a04br3_charger_init(void);
void a04br3_registers_init(void);
void a04br3_gpu_init(void);
void boot_memory_snapshot(uint64_t out[7]);
void boot_home_perf_sample(void);
void boot_home_irq_init(unsigned);
unsigned boot_home_irq_hook_available(void);
void boot_home_irq_report(void);
void native_keystore_probe(void);
int sheng_boot_watchdog_policy(void *process, uint64_t scratch);
int a04br3_powervrprobed_spawn(void *process, uint64_t scratch);
void native_powervr_metal_publish(void);   // package/powervr/NativePowerVRMetal.cpp (a04br3-gpu-metal=1)
extern volatile uint64_t native_msdc_verified_cache_stats[3] __attribute__((weak_import));
extern volatile uint32_t native_keyevents_stage;
extern volatile int native_keyevents_status;
// Existing kern_exec.c addon installer and union-mount diagnostics.
extern volatile int native_gpu_addon_files, native_gpu_addon_written, native_gpu_addon_same;
extern volatile int native_gpu_addon_failed, native_gpu_addon_error;
extern volatile int native_gpu_addon_stage, native_gpu_addon_status;
extern volatile int native_gpu_addon2_stage, native_gpu_addon2_status;
extern volatile int native_gpu_addon3_stage, native_gpu_addon3_status;
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

// The display state on UART with each process list: package/LegacyDisplayProbe.cpp registers its report here (the first
// kernel pass links a display stub that registers nothing, so the board archive references no display symbol).
static void (*volatile gDisplayReport)(void);

// The thread states of launchd, backboardd, SpringBoard and the other targets of package/BootProcessProbe.c (UniXNU-tb128fu):
// threads, running, waiting, the first thread's state/priority, wait event, continuation, user PC and shared region slide.
// They tell a process that computes (r>0, user PC moving) from one that waits (W the same each pass).
extern "C" {
extern char boot_process_rows[10][120];
void boot_process_capture(void);
void boot_process_snapshot(void);
}
// The user stacks of the traced processes (backboardd = role 0, SpringBoard = role 1, with boot-ui-follow=1 the process
// SpringBoard sends to = role 3 and the one that process sends to = role 0) from package/BootUiTraceProbe.c
// (UniXNU-tb128fu): the main thread's registers and frame chain, then the other thread groups. Unslid = address - cache slide.
extern "C" unsigned boot_ui_trace_role_rows(unsigned role, char rows[][120], unsigned max, unsigned detail);
// BootProcessProbe.c fills the rows of processes it did not find with "<name>: absent from this process pass".
static bool a04br3_row_absent(const char *row)
{
  for (; *row; ++row)
    if (!strncmp(row, ": absent", 8)) return true;
  return false;
}
// Secondary-CPU bring-up counters of ArmCpu (sheng-smp=1): requested/running cores and each core's step and PSCI result.
extern "C" {
extern volatile uint32_t armcpu_smp_requested, armcpu_smp_running, armcpu_smp_step[8];
extern volatile int32_t armcpu_smp_psci_rc[8];
}
static void a04br3_thread_report(void)
{
  IOLog("a04br3 smp: requested %u running %u steps %u/%u/%u/%u psci %d/%d/%d/%d\n", armcpu_smp_requested, armcpu_smp_running,
        armcpu_smp_step[0], armcpu_smp_step[1], armcpu_smp_step[2], armcpu_smp_step[3], armcpu_smp_psci_rc[0],
        armcpu_smp_psci_rc[1], armcpu_smp_psci_rc[2], armcpu_smp_psci_rc[3]);
  // Full thread/stack walks are opt-in: the process heartbeat must not turn every
  // normal frame-rate observation into a synchronous dump of all UI threads.
  static int stacks = -1;
  if (stacks < 0) {
    unsigned enabled = 0;
    PE_parse_boot_argn("a04br3-stacklog", &enabled, sizeof(enabled));
    stacks = enabled != 0;
  }
  if (!stacks) return;
  uint64_t memory[7];
  boot_memory_snapshot(memory);
  IOLog("a04br3 memory: approx pagesz=%llu free=%llu active=%llu inactive=%llu wired=%llu compressor=%llu speculative=%llu\n",
        memory[0], memory[1], memory[2], memory[3], memory[4], memory[5], memory[6]);
  struct native_powervr_bo_live_record bos;
  native_powervr_bo_live_snapshot(&bos);
  if (bos.enabled) {
    IOLog("a04br3 BO live: objects-only normal count=%llu bytes=%llu peak_count=%llu peak_bytes=%llu\n",
          bos.normal.count, bos.normal.bytes, bos.normal.peak_count, bos.normal.peak_bytes);
    IOLog("a04br3 BO live: objects-only host-import count=%llu bytes=%llu peak_count=%llu peak_bytes=%llu\n",
          bos.host_import.count, bos.host_import.bytes, bos.host_import.peak_count, bos.host_import.peak_bytes);
  }
  boot_process_capture();
  boot_process_snapshot();
  for (unsigned i = 0; i < 10; ++i)
    if (boot_process_rows[i][0] && !a04br3_row_absent(boot_process_rows[i])) IOLog("a04br3 threads: %s\n", boot_process_rows[i]);
  static char rows[32][120];
  for (unsigned role = 0; role < 4; ++role) {
    unsigned n = boot_ui_trace_role_rows(role, rows, 32, 1);
    for (unsigned i = 0; i < n; ++i) IOLog("a04br3 stack: %s\n", rows[i]);
  }
}
extern "C" void a04br3_display_report_set(void (*report)(void)) { gDisplayReport = report; }
static void a04br3_display_report(void)
{
  if (void (*report)(void) = gDisplayReport) report();
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

// Existing process-report thread only: no storage hot-path logging.
void msdcCacheReport()
{
  if (!native_msdc_verified_cache_stats) return;
  const uint64_t hits = __atomic_load_n(&native_msdc_verified_cache_stats[0], __ATOMIC_RELAXED);
  const uint64_t misses = __atomic_load_n(&native_msdc_verified_cache_stats[1], __ATOMIC_RELAXED);
  const uint64_t reused = __atomic_load_n(&native_msdc_verified_cache_stats[2], __ATOMIC_RELAXED);
  if (hits || misses || reused)
    IOLog("a04br3 msdc vcache: hits=%llu misses=%llu reused_bytes=%llu\n",
          (unsigned long long)hits, (unsigned long long)misses, (unsigned long long)reused);
}

void procLogThread(void *, wait_result_t)
{
  unsigned perf = 0;
  PE_parse_boot_argn("a04br3-home-perf", &perf, sizeof(perf));
  uint64_t cacheReportSeconds = 0;
  for (unsigned round = 1;; ++round) {
    IOSleep(gProcLogSeconds * 1000);
    if (perf != 1) {
      static ProcLine line;
      line = {};
      proc_iterate(kProcAll, procCallout, &line, nullptr, nullptr);
      procFlush(line);
      IOLog("a04br3 procs: round %u, %u processes\n", round, line.count);
    }
    a04br3_display_report();
    a04br3_thread_report();
    if (perf == 1) {
      uint64_t m[7]; struct boot_memory_queue_record q;
      boot_memory_snapshot(m); boot_memory_queue_snapshot(&q);
      IOLog("a04br3 perf vm pagesize=%llu free=%llu active=%llu inactive=%llu wired=%llu compressor=%llu speculative=%llu throttled=%llu pageable_internal=%llu pageable_external=%llu mode=%d active_compressor=%u active_swap=%u\n",
            m[0],m[1],m[2],m[3],m[4],m[5],m[6],q.throttled,q.pageable_internal,q.pageable_external,q.compressor_mode,q.compressor_active,q.swap_active);
      boot_home_perf_sample();
    }
    boot_home_irq_report();
    cacheReportSeconds += gProcLogSeconds;
    if (cacheReportSeconds >= 60) {
      cacheReportSeconds %= 60;
      msdcCacheReport();
    }
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
  a04br3_registers_init(); // shared I2C0/I2C2 mux lock before either thread
  a04br3_charger_init(); // explicit opt-in; separate I2C2 owner, no PWRAP
  unsigned irqPid = 0;
  if (!PE_parse_boot_argn("native-irq-pid", &irqPid, sizeof(irqPid))) {
    unsigned perf = 0;
    if (boot_home_irq_hook_available() && PE_parse_boot_argn("a04br3-home-perf", &perf, sizeof(perf)) && perf == 1)
      irqPid = 1;  // diagnostic kernel + existing opt-in: sample launchd
  }
  boot_home_irq_init(irqPid);
  a04br3_proclog_init();
  a04br3_gpu_init();   // package/A04BR3Gpu.cpp (a04br3-gpu=1)
  unsigned events = 0;
  if (PE_parse_boot_argn("offline-key-events", &events, sizeof(events)) && events == 1) {
    native_keystore_probe();
    IOLog("a04br3: AppleKeyStore stand-in stage %u status 0x%x\n", native_keyevents_stage, (unsigned)native_keyevents_status);
  }
}

extern "C" int unixnu_board_launch(void *process, uint64_t scratch)
{
  const int policy = sheng_boot_watchdog_policy(process, scratch);
  IOLog("a04br3: GPU addon before probe: files %d written %d same %d failed %d error %d; "
        "mount extensions stage %d status %d, daemons stage %d status %d, wallpaper stage %d status %d\n",
        native_gpu_addon_files, native_gpu_addon_written, native_gpu_addon_same,
        native_gpu_addon_failed, native_gpu_addon_error, native_gpu_addon_stage, native_gpu_addon_status,
        native_gpu_addon2_stage, native_gpu_addon2_status, native_gpu_addon3_stage, native_gpu_addon3_status);
  // Not fatal: its result is printed and kept in a04br3_powervrprobed_status, never returned (a nonzero result stops the boot).
  a04br3_powervrprobed_spawn(process, scratch);
  // The PowerVR accelerator for the original Metal discovery, before launchd starts backboardd (its own thread; a04br3-gpu-metal=1).
  native_powervr_metal_publish();
  return policy;
}
