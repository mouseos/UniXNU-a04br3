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
#include "A04BR3DiagArgs.h"
#include "powervr/native_powervr_bo_stats.h"
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <libkern/libkern.h>

extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

extern "C" {
void a04br3_autoreset_init(void);
void a04br3_cpu_load_report(void);
int a04br3_cpu_load_snapshot(unsigned, uint32_t out[6]);
void a04br3_charger_init(void);
void a04br3_registers_init(void);
void a04br3_gpu_init(void);
void boot_memory_snapshot(uint64_t out[7]);
void boot_home_perf_sample(void);
void boot_home_irq_init(unsigned);
void boot_live_pc_init(unsigned);
void boot_live_pc_report(void);
unsigned boot_home_irq_hook_available(void);
void boot_home_irq_report(void);
void boot_ipi_reason_enable(unsigned);
void boot_ipi_reason_report(void);
void boot_ast_caller_enable(unsigned);
void boot_ast_caller_report(void);
void native_keystore_probe(void);
int sheng_boot_watchdog_policy(void *process, uint64_t scratch);
int a04br3_powervrprobed_spawn(void *process, uint64_t scratch);
int a04br3_memory_bench_spawn(void *process, uint64_t scratch);
void native_powervr_submit_perf_enable(unsigned);
void native_powervr_submit_perf_report(void);
void native_powervr_io_trace_enable(unsigned);
void native_powervr_submit_stage_enable(unsigned, unsigned);
void native_powervr_io_trace_report(void);
void native_powervr_metal_publish(void);   // package/powervr/NativePowerVRMetal.cpp (a04br3-gpu-metal=1)
extern volatile uint64_t native_msdc_verified_cache_stats[3] __attribute__((weak_import));
extern volatile uint64_t native_msdc_irq_stats[4] __attribute__((weak_import));
extern volatile uint64_t native_msdc_worker_stats[4] __attribute__((weak_import));
extern volatile uint64_t native_msdc_irq_mask_failures __attribute__((weak_import));
extern volatile uint32_t native_msdc_iostat_enabled __attribute__((weak_import));
extern volatile uint64_t native_msdc_transport_stats[36] __attribute__((weak_import));
extern volatile uint64_t native_msdc_block_offer_stats[12] __attribute__((weak_import));
extern volatile uint64_t native_msdc_block_queue_stats[3] __attribute__((weak_import));
void native_root_timing_snapshot(uint64_t out[6]) __attribute__((weak_import));
void native_cs_trace_report(void) __attribute__((weak_import));
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
extern volatile uint32_t armcpu_core_timer[8], armcpu_core_sgi[8], armcpu_gic_boot[8];
extern volatile uint64_t armcpu_ipi_sent, armcpu_ipi_dropped;
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

// Existing worker only. Snapshot fields are separate relaxed atomics, so their values need not represent one instant.
// Transport wall includes lock wait, DMA and copying; it is neither CPU time nor per-process attribution.
void msdcRootTimingReport(unsigned enabled)
{
  if (enabled != 1 || !native_root_timing_snapshot) return;
  uint64_t begin = 0, end = 0, values[6] = {};
  absolutetime_to_nanoseconds(mach_absolute_time(), &begin);
  native_root_timing_snapshot(values);
  absolutetime_to_nanoseconds(mach_absolute_time(), &end);
  IOLog("a04br3 msdc root timing: begin_ns=%llu end_ns=%llu read_calls=%llu read_bytes=%llu read_ns=%llu verify_calls=%llu verify_bytes=%llu verify_ns=%llu\n",
        (unsigned long long)begin, (unsigned long long)end,
        (unsigned long long)values[0], (unsigned long long)values[1], (unsigned long long)values[2],
        (unsigned long long)values[3], (unsigned long long)values[4], (unsigned long long)values[5]);
}

// Diagnostic-only cumulative counters. Each load is atomic, but fields can
// advance independently between loads; the group is not one atomic snapshot.
void msdcIrqReport(unsigned enabled)
{
  if (enabled != 1) return;
  if (native_msdc_irq_stats) {
    uint64_t v[4];
    for (unsigned i = 0; i < 4; ++i) v[i] = __atomic_load_n(&native_msdc_irq_stats[i], __ATOMIC_RELAXED);
    IOLog("a04br3 msdc irq: captured=%llu waits=%llu no_delivery=%llu wait_ns=%llu (non-atomic snapshot)\n",
          (unsigned long long)v[0], (unsigned long long)v[1], (unsigned long long)v[2], (unsigned long long)v[3]);
  } else IOLog("a04br3 msdc irq: unavailable\n");
  if (native_msdc_worker_stats) {
    uint64_t v[4];
    for (unsigned i = 0; i < 4; ++i) v[i] = __atomic_load_n(&native_msdc_worker_stats[i], __ATOMIC_RELAXED);
    IOLog("a04br3 msdc worker: queued=%llu completed=%llu direct_fallback=%llu reserved=%llu (non-atomic snapshot)\n",
          (unsigned long long)v[0], (unsigned long long)v[1], (unsigned long long)v[2], (unsigned long long)v[3]);
  } else IOLog("a04br3 msdc worker: unavailable\n");
  if (&native_msdc_irq_mask_failures) {
    uint64_t failures = __atomic_load_n(&native_msdc_irq_mask_failures, __ATOMIC_RELAXED);
    IOLog("a04br3 msdc irq mask_failures=%llu\n", (unsigned long long)failures);
  } else IOLog("a04br3 msdc irq mask_failures=unavailable\n");
}

// Read all counters before emitting lines. The timestamps bracket the loads,
// not the entire heartbeat, and the independently atomic fields can race.
void msdcIoReport(unsigned enabled)
{
  if (enabled != 1) return;
  if (!&native_msdc_iostat_enabled) { IOLog("a04br3 msdc io: unavailable\n"); return; }
  if (!__atomic_load_n(&native_msdc_iostat_enabled, __ATOMIC_ACQUIRE)) { IOLog("a04br3 msdc io: disabled\n"); return; }
  bool haveTransport = native_msdc_transport_stats != nullptr;
  bool haveOffer = native_msdc_block_offer_stats != nullptr;
  bool haveQueue = native_msdc_block_queue_stats != nullptr;
  static uint64_t sequence = 0; // only the process-report thread calls this helper
  const uint64_t sampleSeq = ++sequence;
  uint64_t transfer[36] = {}, offer[12] = {}, queue[3] = {}, begin = 0, end = 0;
  absolutetime_to_nanoseconds(mach_absolute_time(), &begin);
  if (haveTransport) for (unsigned i = 0; i < 36; ++i) transfer[i] = __atomic_load_n(&native_msdc_transport_stats[i], __ATOMIC_RELAXED);
  if (haveOffer) for (unsigned i = 0; i < 12; ++i) offer[i] = __atomic_load_n(&native_msdc_block_offer_stats[i], __ATOMIC_RELAXED);
  if (haveQueue) for (unsigned i = 0; i < 3; ++i) queue[i] = __atomic_load_n(&native_msdc_block_queue_stats[i], __ATOMIC_RELAXED);
  absolutetime_to_nanoseconds(mach_absolute_time(), &end);
  IOLog("a04br3 msdc io sample seq=%llu begin_ns=%llu end_ns=%llu non_atomic=1\n",
        (unsigned long long)sampleSeq, (unsigned long long)begin, (unsigned long long)end);
  static const char * const modes[] = {"dma_read", "pio_read", "write"};
  static const char * const bins[] = {"le4k", "4k_16k", "gt16k"};
  if (haveTransport) {
    for (unsigned mode = 0; mode < 3; ++mode) for (unsigned bin = 0; bin < 3; ++bin) {
      unsigned index = (mode * 3 + bin) * 4;
      IOLog("a04br3 msdc transfer seq=%llu mode=%s size=%s calls=%llu ok_bytes=%llu errors=%llu elapsed_ns=%llu\n",
            (unsigned long long)sampleSeq, modes[mode], bins[bin], (unsigned long long)transfer[index], (unsigned long long)transfer[index+1],
            (unsigned long long)transfer[index+2], (unsigned long long)transfer[index+3]);
    }
  } else IOLog("a04br3 msdc transfer seq=%llu unavailable\n", (unsigned long long)sampleSeq);
  if (haveOffer) {
    for (unsigned mode = 0; mode < 2; ++mode) for (unsigned bin = 0; bin < 3; ++bin) {
      unsigned index = (mode * 3 + bin) * 2;
      IOLog("a04br3 msdc offered seq=%llu mode=%s size=%s calls=%llu bytes=%llu\n",
            (unsigned long long)sampleSeq, mode ? "write" : "read", bins[bin],
            (unsigned long long)offer[index], (unsigned long long)offer[index+1]);
    }
  } else IOLog("a04br3 msdc offered seq=%llu unavailable\n", (unsigned long long)sampleSeq);
  if (haveQueue) IOLog("a04br3 msdc queue seq=%llu started=%llu wait_ns=%llu max_wait_ns=%llu\n",
                       (unsigned long long)sampleSeq, (unsigned long long)queue[0],
                       (unsigned long long)queue[1], (unsigned long long)queue[2]);
  else IOLog("a04br3 msdc queue seq=%llu unavailable\n", (unsigned long long)sampleSeq);
  IOLog("a04br3 msdc io end seq=%llu rows=%u\n", (unsigned long long)sampleSeq,
        (haveTransport ? 9u : 0u) + (haveOffer ? 6u : 0u) + (haveQueue ? 1u : 0u));
}

// Called only by the opt-in PMU context worker, outside PMU lease and locks.
// Maps and reads the same board clock registers used by the diagnostic below. It does
// not use WACS: CPU rail voltage and temperature remain unavailable.
extern "C" void a04br3_cpu_context_sample(unsigned round, unsigned cpu, unsigned phase)
{
  static volatile uint8_t *infra, *apmixed;
  static bool attempted;
  uint64_t begin, end;
  __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(begin) :: "memory");
  if (!attempted) {
    attempted = true;
    infra = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10001000, 0x1000));
    apmixed = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10018000, 0x1000));
  }
  uint32_t mux=0, dcm=0, con0=0, con1=0, power=0, ticks[6]={};
  const bool clocks=infra && apmixed;
  if (clocks) {
    mux=*reinterpret_cast<volatile uint32_t *>(infra);
    dcm=*reinterpret_cast<volatile uint32_t *>(infra+0x14);
    con0=*reinterpret_cast<volatile uint32_t *>(apmixed+0x100);
    con1=*reinterpret_cast<volatile uint32_t *>(apmixed+0x104);
    power=*reinterpret_cast<volatile uint32_t *>(apmixed+0x110);
    __asm__ volatile("dmb oshld" ::: "memory");
  }
  const int load=a04br3_cpu_load_snapshot(cpu,ticks);
  __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(end) :: "memory");
  IOLog("a04br3 cpu-context round=%u cpu=%u phase=%u begin=%llu end=%llu clock=%u load=%u rail=unknown temp=unknown\n",round,cpu,phase,begin,end,(unsigned)clocks,(unsigned)load);
  if (clocks) IOLog("a04br3 cpu-context-clk round=%u cpu=%u phase=%u infra=%08x dcm=%08x con0=%08x con1=%08x pll_pwr=%08x\n",round,cpu,phase,mux,dcm,con0,con1,power);
  if (load) IOLog("a04br3 cpu-context-load round=%u cpu=%u phase=%u user=%u system=%u idle=%u nice=%u state=%u/%u ticks32=1\n",round,cpu,phase,ticks[0],ticks[1],ticks[2],ticks[3],ticks[4],ticks[5]);
}

// Diagnostic sampling only. Registers and per-core counters are independently read,
// not an atomic snapshot. CNTFRQ is the architectural timer, not CPU frequency.
void cpuDiagnosticReport()
{
  static volatile uint8_t *infra;
  static volatile uint8_t *apmixed;
  static bool attempted;
  if (!attempted) {
    attempted = true;
    infra = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10001000, 0x1000));
    apmixed = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10018000, 0x1000));
  }
  uint64_t timerFrequency;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(timerFrequency));
  if (infra && apmixed) {
    const uint32_t mux = *reinterpret_cast<volatile uint32_t *>(infra);
    // Linux MT8167: INFRA_TOPCKGEN_DCMDBC at INFRACFG+0x14.
    // Raw only: its switch bit does not establish a frequency divisor.
    const uint32_t dcm = *reinterpret_cast<volatile uint32_t *>(infra + 0x14);
    const uint32_t con0 = *reinterpret_cast<volatile uint32_t *>(apmixed + 0x100);
    const uint32_t con1 = *reinterpret_cast<volatile uint32_t *>(apmixed + 0x104);
    const uint32_t power = *reinterpret_cast<volatile uint32_t *>(apmixed + 0x110);
    __asm__ volatile("dmb oshld" ::: "memory");
    IOLog("a04br3 perf cpu-clk raw infra=%08x mux=%u dcm=%08x armpll_con0=%08x con1=%08x pwr=%08x cntfrq=%llu (non-atomic; cntfrq is timer)\n",
          mux, (mux >> 2) & 3, dcm, con0, con1, power, timerFrequency);
  } else {
    IOLog("a04br3 perf cpu-clk unknown map infra=%u apmixed=%u cntfrq=%llu\n",
          infra != nullptr, apmixed != nullptr, timerFrequency);
  }
  IOLog("a04br3 perf irq counters timer=%u/%u/%u/%u ipi=%u/%u/%u/%u (per-core cumulative; non-atomic)\n",
        armcpu_core_timer[0], armcpu_core_timer[1], armcpu_core_timer[2], armcpu_core_timer[3],
        armcpu_core_sgi[0], armcpu_core_sgi[1], armcpu_core_sgi[2], armcpu_core_sgi[3]);
  IOLog("a04br3 perf ipi sent=%llu dropped=%llu gic_typer=%08x (approx cumulative)\n",
        armcpu_ipi_sent, armcpu_ipi_dropped, armcpu_gic_boot[3]);
}

void procLogOptions(unsigned &perf, unsigned &rootTiming, unsigned &msdcPerf)
{
  unsigned ioStats = 0;
  perf = rootTiming = msdcPerf = 0;
  PE_parse_boot_argn("a04br3-home-perf", &perf, sizeof(perf));
  PE_parse_boot_argn("a04br3-msdc-perf", &msdcPerf, sizeof(msdcPerf));
  PE_parse_boot_argn("native-msdc-iostat", &ioStats, sizeof(ioStats));
  PE_parse_boot_argn("native-root-timing", &rootTiming, sizeof(rootTiming));
  if (ioStats == 1) msdcPerf = 1;
}

void procLogThread(void *, wait_result_t)
{
  unsigned perf, rootTiming, msdcPerf, overlapTrace = 0;
  procLogOptions(perf, rootTiming, msdcPerf);
  PE_parse_boot_argn("native-io-trace", &overlapTrace, sizeof(overlapTrace));
  if (overlapTrace == 1 && !native_cs_trace_report) IOLog("CS overlap unavailable: kernel trace not built\n");
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
      msdcIrqReport(msdcPerf);
      msdcIoReport(msdcPerf);
      cpuDiagnosticReport();
    }
    boot_home_irq_report();
    boot_live_pc_report();
    boot_ipi_reason_report();
    boot_ast_caller_report();
    native_powervr_submit_perf_report();
    native_powervr_io_trace_report();
    msdcRootTimingReport(rootTiming);
    if (overlapTrace == 1 && native_cs_trace_report) native_cs_trace_report();
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
  if (!PE_parse_boot_argn("a04br3-proclog", &gProcLogSeconds, sizeof(gProcLogSeconds))) {
    // Short form frees boot-argument space for simultaneous diagnostics.
    if (!PE_parse_boot_argn("pl", &gProcLogSeconds, sizeof(gProcLogSeconds))) return;
    if (gProcLogSeconds == 1) gProcLogSeconds = 10;
  }
  if (!gProcLogSeconds) return;
  thread_t thread;
  if (kernel_thread_start(procLogThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

static void a04br3_cpu_load_thread(void *, wait_result_t)
{
  for (;;) { IOSleep(10000); a04br3_cpu_load_report(); }
}

static void a04br3_cpu_load_init(void)
{
  unsigned enabled = 0;
  unsigned ioStats = 0;
  PE_parse_boot_argn("a04br3-cpu-load", &enabled, sizeof(enabled));
  PE_parse_boot_argn("native-msdc-iostat", &ioStats, sizeof(ioStats));
  if (enabled != 1 && ioStats != 1) return;
  thread_t thread;
  kern_return_t status = kernel_thread_start(a04br3_cpu_load_thread, nullptr, &thread);
  if (status == KERN_SUCCESS) thread_deallocate(thread);
  else IOLog("a04br3 cpu-load thread_error=%d\n", status);
}

#include "A04BR3EarlyCopy.h"

extern "C" void unixnu_board_init(void)
{
  a04br3_cpu_load_init();
  a04br3_autoreset_init();
  a04br3_early_copy_probe(); // opt-in, bounded; before root mount and launchd
  a04br3_registers_init(); // shared I2C0/I2C2 mux lock before either thread
  a04br3_charger_init(); // explicit opt-in; separate I2C2 owner, no PWRAP
  unsigned irqPid = 0;
  if (!PE_parse_boot_argn("native-irq-pid", &irqPid, sizeof(irqPid))) {
    unsigned perf = 0;
    if (boot_home_irq_hook_available() && PE_parse_boot_argn("a04br3-home-perf", &perf, sizeof(perf)) && perf == 1)
      irqPid = 1;  // diagnostic kernel + existing opt-in: sample launchd
  }
  boot_home_irq_init(irqPid);
  unsigned livePc = 0;
  PE_parse_boot_argn("a04br3-live-pc", &livePc, sizeof(livePc));
  boot_live_pc_init(livePc == 1 ? 1 : 0);
  unsigned ipiReasonPerf = 0;
  PE_parse_boot_argn("a04br3-home-perf", &ipiReasonPerf, sizeof(ipiReasonPerf));
  boot_ipi_reason_enable(ipiReasonPerf);
  unsigned astCaller = 0;
  PE_parse_boot_argn("asc", &astCaller, sizeof(astCaller));
  boot_ast_caller_enable(astCaller);
  unsigned gpuSubmitPerf = 0;
  // Existing diagnostic option avoids extending a near-capacity boot command line.
  // An explicit GPU setting takes precedence, including 0 to disable it.
  if (!PE_parse_boot_argn("a04br3-gpu-submit-perf", &gpuSubmitPerf, sizeof(gpuSubmitPerf)))
    PE_parse_boot_argn("a04br3-home-perf", &gpuSubmitPerf, sizeof(gpuSubmitPerf));
  native_powervr_submit_perf_enable(gpuSubmitPerf);
  unsigned gpuIoTrace = 0;
  if (!PE_parse_boot_argn("a04br3-gpu-io-trace", &gpuIoTrace, sizeof(gpuIoTrace)) &&
      !PE_parse_boot_argn("a04br3-gio", &gpuIoTrace, sizeof(gpuIoTrace)))
    PE_parse_boot_argn("gi", &gpuIoTrace, sizeof(gpuIoTrace));
  unsigned gpuStage = a04br3_diag_arg(PE_parse_boot_argn,"a04br3-gpu-stage","gs");
  native_powervr_submit_stage_enable(gpuStage, gpuIoTrace);
  native_powervr_io_trace_enable(gpuIoTrace);
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
  a04br3_memory_bench_spawn(process, scratch);
  // The PowerVR accelerator for the original Metal discovery, before launchd starts backboardd (its own thread; a04br3-gpu-metal=1).
  native_powervr_metal_publish();
  return policy;
}
