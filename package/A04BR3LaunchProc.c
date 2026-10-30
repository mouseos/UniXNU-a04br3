// SPDX-License-Identifier: MIT
// Two proc fields for the app launch breakdown of package/BootProcessProbe.c (a04br3-input-latency=2), which is compiled with
// the osfmk record and sees struct proc as opaque. Built by scripts/build-a04br3-board.py with the kernel's record for
// kern_exec.c (proc_internal.h), as A04BR3ProbeLaunch.c. Read without the proc lock: both are written once per process
// (fork, and the first bsdthread_register) and the reader only compares them with zero or prints them.
#include <sys/param.h>
#include <sys/proc_internal.h>
#include <sys/resourcevar.h>

uint64_t a04br3_proc_threadstart(void *p);
uint64_t a04br3_proc_start_abs(void *p);

// Set by bsdthread_register, which libpthread calls from libSystem's initializer: dyld has loaded and bound the process's
// images and started their initializers.
uint64_t a04br3_proc_threadstart(void *p)
{
  return p ? (uint64_t)((proc_t)p)->p_threadstart : 0;
}

// The process's start in mach absolute time (fork_create_child: microtime_with_abstime), as proc_starttime_kdp reads it.
uint64_t a04br3_proc_start_abs(void *p)
{
  proc_t proc = (proc_t)p;
  return proc && proc->p_stats ? proc->p_stats->ps_start : 0;
}
