# TAB-A04-BR3 synthetic memory copy measurement

The diagnostic is off in ordinary builds and does not change the normal
rendering path. On a macOS UniXNU build host with the usual Xcode/iPhoneOS SDK,
IPSW/network and platform-seed prerequisites, from a clean UniXNU checkout run:

```sh
/path/to/UniXNU-a04br3/scripts/a04br3-memory-build.sh --enable-memory-bench
```

This calls the ordinary `unixnu defconfig`, `tree`, `build kernel` and `build
payload` commands, the device pre-build, and the standard seed/trust tooling.
After the pre-build it parses the seed UUID from JSON and exports it for the
final kernel build; it does not source the generated shell file. Seed metadata,
mounted volume identity, the generated environment record and the kernel UUID
string must agree. A completion manifest is written only after the final
kernel build succeeds; it binds kernel hash, Data and trust hashes, UUID, clean
source commits and submodule state, pinned XNU reference and patched-source
diff, effective configuration and build flags.
Payload construction refuses a missing or changed manifest. String presence in
the binary is an integrity check, not proof of the runtime UUID selection.
Source checkouts must be clean and submodules initialized.
`RUNNER_TEMP` defaults to `out/a04br3-memory-inputs`; no existing seed or trust
file is requested. The build enables the PowerVR probe build and Metal publish
mode, preserving GPU composition (`sheng-fb-gpu=1`); it adds the diagnostic
boot argument and diagnostic heartbeat. The generated owner trust bundle
includes the signed benchmark CDHash. `--skip-setup` can optionally be passed
after the explicit flag. The usual UniXNU configuration of a platform seed is
still required. Build prerequisites and the signed GPU components remain those
of the existing board pre-build. This clean build recipe has not yet been run
on macOS, and GPU functionality of its output is not verified.

The board hook spawns `/private/var/ngpu/bin/a04br3-memory-copy-bench` only
when `a04br3-membench=1` is present. The child opens `/dev/console`, waits 180
seconds, checks free VM pages, and if at least 64 MiB are reported free,
allocates two 16 MiB buffers. It initializes the source and writes each 4 KiB
of the destination through a volatile pointer before timing one copy; it
compares the buffers after timing, writes the result to the console, and exits.
The free-page check is conservative, racy and **not a safety guarantee**; it
can skip even if reclaimable memory exists. A boot that has not reached idle at
180 seconds is not an idle measurement. No repeated load or rendering fallback
is introduced. Nonzero spawn errno, missing console, free-page refusal,
allocation failure, process exit, and absent results must be distinguished.

The normal standalone `--run size_mib` accepts 1–64 MiB per buffer: this is an
input limit, not a safe allocation limit. Output includes monotonic start/end,
elapsed time, logical source bytes, and verification. Effective copy MiB/s =
logical_bytes * 1e9 / elapsed_ns / 1048576. Count logical bytes once. These
wall-clock CPU-copy measurements include scheduling and interrupts; cache,
write allocation, DRAM activity from other masters, placement, CPU frequency,
and memcpy implementation affect them. They are not DRAM controller traffic or
total system bandwidth. Neither the buffer size nor volatile page touching
proves cache misses. A validated controller counter is still needed for actual
system DRAM traffic.

Record source and image IDs, workload, free pages and boot identity. Separate
idle and real-workload observations from the artificial test, and compare
before/after baselines. For distributions, run separately with recovery gaps;
the automatic path performs only one pass per boot. Verify the relationship
between user `CLOCK_MONOTONIC` and kernel timestamps before joining them.
Heartbeat lines are sequential, not an atomic snapshot. Target compilation,
signature, trust, spawn, console and measurement are all unverified until the
corresponding actual image and UART have been checked. Use REA to inspect the
actual signed target binary if code-generation evidence is required.

Host checks:

```sh
python3 scripts/tests/test_memory_copy_bench.py
python3 scripts/tests/test_memory_bench_launch.py
python3 scripts/tests/test_memory_payload.py
python3 scripts/tests/test_a04br3_pre_build_seed_reuse.py
```
