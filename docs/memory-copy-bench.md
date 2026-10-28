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
The generated script tree is checked byte-for-byte against the base and device
source overlays before it is used. Unexpected files, cached bytecode and
symlinks in that tree are refused. Verified copy timestamps are stabilized
so UniXNU's size/mtime-based reassembly keeps the intended overlay; the tree
is checked again when the kernel is sealed and before payload construction.
If stale files remain from another build, select a fresh `UNIXNU_OUT` or repair
the generated tree before retrying. Payload construction refuses a missing or changed manifest. String presence in
the binary is an integrity check, not proof of the runtime UUID selection.
Source checkouts must be clean and submodules initialized. Untracked files in
the selected output root and `RUNNER_TEMP` are treated as generated, while
tracked files are never exempted. Output roots containing tracked source files
or the repository root itself are rejected; untracked files elsewhere are also
rejected.
`RUNNER_TEMP` defaults to `out/a04br3-memory-inputs`; no existing seed or trust
file is requested. Each run uses a separate directory inside that root, and
only after the payload succeeds is `current` atomically switched to that run.
Its `current/trustcache`, `current/data.dbr`, `current/kernel.macho` and
`current/a04br3-memory-inputs.json`, `current/payload.img` and
`current/payload.json` are the completed set. The payload report is checked
against the staged kernel, trust and payload image before publication. Source/tree
preflight happens before trust creation. On failure the new directory is
removed and the previous current set remains intact. Once publication has
been attempted, the new directory is retained even if the process reports an
error; inspect `current` before removing any orphaned run. Cooperating memory
builds targeting the same resolved `UNIXNU_OUT` are serialized by an atomic
directory lock; other build commands do not honor that lock. A killed process
may leave a stale `.a04br3-memory-build.lock` directory: confirm that no build
is using that output before removing it. The main UniXNU output and build
caches may still change on a failed build. `UNIXNU_OUT` selects the UniXNU output root for both the
pre-build tree and the completed-kernel manifest, with the same path expansion
as UniXNU.

A fresh run refuses existing `current`, `owner-data`, `data-32.dbr`,
`data.dbr`, or `data-probe.dbr` in the `RUNNER_TEMP` root; it leaves the previous
manifest and seed files intact. To repeat using a complete compatible prior
seed, retain the nonempty `data.dbr` and baseline `data-32.dbr` plus valid
`owner-data/seed.json` in `current` (or in the legacy root), then run:

```sh
A04BR3_REUSE_DATA_SEED=1 /path/to/UniXNU-a04br3/scripts/a04br3-memory-build.sh --enable-memory-bench
```

Reuse rebuilds from the retained policy-seeded baseline, verifies the mounted
Data UUID against the metadata, and creates fresh staged Data and owner trust.
It is not a promise to preserve an existing persistent store's contents. For a
new seed, choose an empty `RUNNER_TEMP`; incomplete or UUID-mismatched reuse
inputs are rejected. The build enables the PowerVR probe build and Metal publish
mode, preserving GPU composition (`sheng-fb-gpu=1`); it adds the diagnostic
boot argument and diagnostic heartbeat. The profile enables
`native-msdc-iostat=1`, which also starts the per-CPU load reporter in this
device revision. MSDC timing still requires a matching NativeMsdc source;
missing counters are not zero. The integration was prepared against UniXNU
`b9824adabfa7206dc07978f183474960856085fc` with NativeMsdc
`965ecd384a9fbd33b21674380edddd2bd57a4acc`; the source and submodule must be
pinned and checked for the target build. The generated owner trust bundle
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
python3 scripts/tests/test_memory_real_git.py
UNIXNU_ROOT=/path/to/UniXNU python3 scripts/tests/test_memory_tree_scripts.py
UNIXNU_ROOT=/path/to/UniXNU python3 scripts/tests/test_memory_iostat_profile.py
python3 scripts/tests/test_a04br3_pre_build_seed_reuse.py
```
