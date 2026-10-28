// SPDX-License-Identifier: MIT
// a04br3-powervrprobed=1: start the PowerVR user-space probe (package/powervr/user/powervrprobed.c) once, from the board
// launch hook unixnu_board_launch() (package/A04BR3Board.cpp), i.e. as init (pid 1) after the root, SystemOS and Data mounts
// and before launchd is executed. Built by scripts/build-a04br3-board.py with the kernel's record for kern_exec.c, as
// BootWatchdogPolicy.c.
//
// Why not a launchd job: iPadOS 17's launchd takes its jobs from the signed compiled cache (/System/Library/xpc/launchd.plist),
// ignores the ProgramArguments of an overlay plist and never enumerates a new label (UniXNU-tb128fu board.toml, tb128fu-038/039:
// the original iomfb_fdr_loader ran although the GPU addon's plist of that name was visible; UniXNU-droidvm board.toml
// owner-service-manager). UniXNU-droidvm (package/DroidVMLaunchPolicy.c spawn_service_manager()) posix_spawns its owner
// binary from the same hook; this is that call for one binary.
//
// The binary is /private/var/ngpu/bin/powervrprobed on the Data volume (scripts/a04br3-seed-powervr-probe.py, or the GPU addon
// of scripts/build-a04br3-powervr-probe.py --addon), trusted through the owner trust bundle (--owner-trust-cache). With the
// owner SystemOS union (CONFIG_XNU_OWNER_UNION_SYSTEMOS=y) the dyld shared cache is found at its normal path, so the child
// gets no environment. Never fatal: a missing binary or a refused spawn only prints its errno, and the hook still returns the
// launch policy's status (a nonzero hook result stops the boot). Spawned at most once per boot (load_init_program() may try
// more than one init path).
//
// Scratch use: the hook's scratch page in the init map; bytes 0x400..0x6ff may hold init_envp (the hook patch) and
// BootWatchdogPolicy.c uses up to 0x8ff, so this uses 0xc00..0xcbf.
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/proc.h>
#include <sys/sysproto.h>
#include <pexpert/pexpert.h>

volatile int a04br3_powervrprobed_status = -1;

int a04br3_powervrprobed_spawn(void *process, uint64_t scratch)
{
  static int started;
  uint32_t enabled = 0;
  if (started || !PE_parse_boot_argn("a04br3-powervrprobed", &enabled, sizeof(enabled)) || enabled != 1) return 0;
  static const char path[] = "/private/var/ngpu/bin/powervrprobed";
  const user_addr_t pathAddr = scratch + 0xc00, argvAddr = scratch + 0xc80, envpAddr = scratch + 0xca0, pidAddr = scratch + 0xcb0;
  const user64_addr_t argv[2] = {pathAddr, 0}, envp[1] = {0};
  int error = copyout(path, pathAddr, sizeof(path));
  if (!error) error = copyout(argv, argvAddr, sizeof(argv));
  if (!error) error = copyout(envp, envpAddr, sizeof(envp));
  if (!error) {
    struct posix_spawn_args request = {.pid = pidAddr, .path = pathAddr, .adesc = USER_ADDR_NULL, .argv = argvAddr, .envp = envpAddr};
    int32_t result = 0;
    error = posix_spawn((proc_t)process, &request, &result);
  }
  int32_t pid = 0;
  if (!error) {
    started = 1;
    if (copyin(pidAddr, &pid, sizeof(pid))) pid = 0;
  }
  a04br3_powervrprobed_status = error;
  printf("a04br3: powervrprobed spawn %d (pid %d)\n", error, pid);
  return error;
}


// Independent opt-in; the scratch region is reused after the prior spawn returns.
int a04br3_memory_bench_spawn(void *process, uint64_t scratch)
{
  static int started;
  uint32_t enabled = 0;
  if (started) return 0;
  if (!PE_parse_boot_argn("a04br3-membench", &enabled, sizeof(enabled)))
    PE_parse_boot_argn("mb", &enabled, sizeof(enabled)); // bounded iostat4 profile
  if (enabled != 1 && enabled != 2) return 0;
  static const char path[] = "/private/var/ngpu/bin/a04br3-memory-copy-bench";
  static const char normal[] = "--boot-run", early[] = "--boot-run-early";
  const char *option = enabled == 2 ? early : normal;
  size_t optionSize = enabled == 2 ? sizeof(early) : sizeof(normal);
  const user_addr_t pathAddr = scratch + 0xc00, optionAddr = scratch + 0xc40;
  const user_addr_t argvAddr = scratch + 0xc80, envpAddr = scratch + 0xca0, pidAddr = scratch + 0xcb0;
  const user64_addr_t argv[3] = {pathAddr, optionAddr, 0}, envp[1] = {0};
  int error = copyout(path, pathAddr, sizeof(path));
  if (!error) error = copyout(option, optionAddr, optionSize);
  if (!error) error = copyout(argv, argvAddr, sizeof(argv));
  if (!error) error = copyout(envp, envpAddr, sizeof(envp));
  if (!error) {
    struct posix_spawn_args request = {.pid = pidAddr, .path = pathAddr, .adesc = USER_ADDR_NULL, .argv = argvAddr, .envp = envpAddr};
    int32_t result = 0;
    error = posix_spawn((proc_t)process, &request, &result);
  }
  int32_t pid = 0;
  if (!error) { started = 1; if (copyin(pidAddr, &pid, sizeof(pid))) pid = 0; }
  printf("a04br3: memory bench spawn %d (pid %d) mode=%u\n", error, pid, enabled);
  return error;
}
