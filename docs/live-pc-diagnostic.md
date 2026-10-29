# Opt-in launchd timer samples

Build with the companion UniXNU timer patch and `XNU_IRQ_PC_STATS=1`; the default
is zero. Set `a04br3-live-pc=1 a04br3-proclog=10` at boot to observe PID 1.
The separate runtime gate defaults to zero. Existing `native-irq-pid` / home
histogram controls are independent. An absent timer hook is reported as hook=0;
zero samples are not evidence of idle CPU. No binary or hardware behavior is
altered when the runtime gate is off beyond the compiled weak call if the
compile gate was explicitly enabled.

The timer callback records interrupted PC and LR for user mode; for kernel
mode it records the interrupted kernel PC after VM_KERNEL_UNSLIDE and LR=0.
It records process unique ID, exec identity version, thread ID and mode. One
in 16 timer observations is retained in a fixed 64-slot ring for each of four
CPUs. The interrupt path has no shared lock, scan, retrying atomic RMW, or
allocation. `lost` counts samples overwritten before the report, and `torn`
counts slots changed during reading; neither is presented as a valid sample.
The counters and timestamps are non-atomic. Other CPU IDs set `bad_cpu`. Periodic sampling may alias and excludes time with interrupts masked.
It does not calculate CPU time. This is not a saved UPCB or a call stack.

Every report includes the XNU boot session UUID when available, pid,
epoch and monotonic report-start times; samples are not individually
timestamped and are not bounded by the report-start time. Each sample includes
its own uid and exec_version; `unavailable` is not a boot identifier. Follow the
UART boot boundary and do not join separate boots. Samples carry the process
identity version, which XNU increments on exec. Never attribute a sample to an
image row of another version, PID, unique ID or boot. Rows describe a bounded
later image-list observation, not an atomic mapping at every earlier interrupt.
Report start/end timestamps, status (`complete`, `partial`, `failed` or
`changed`), 128-entry truncation, detail completeness and Mach-O UUID /
__TEXT validity flags are explicit. `exec_version` and `dyld_version` are
distinct. A complete status means only that these bounded reads completed and
matched their later checks; it does not make the snapshot atomic. A malformed
or partially unreadable Mach-O reports incomplete, clears UUID/text validity
and values, and must be excluded from address attribution. Consumers must
require `detail_complete=1`, UUID and text validity, a matching fixed binary,
and independently establish that its mapping existed at the sampled time.
`cache_slide_valid=0` means unknown; the cache slide is not the executable or
dyld slide. For a valid individual __TEXT row the candidate image slide is
`load - text_vm`, after confirming the image UUID against the local binary.
The dyld all-image-infos record itself is user-controlled and may be stale,
changed during reading, absent early in boot, or incomplete. Even when the
header and entries compare equal, the result is not atomic and does not prove
that the mapping was present at the sample time; this is explicitly reported
as `mapping_at_sample=unknown`. A dyld load address is printed
separately when the ABI field is populated. Treat missing or invalid values as
unavailable; never invent a slide or combine with another boot's mapping.
The worker reads user VM and can fault or block. UART output of up to 129 image
rows and up to 256 PC rows each period adds substantial diagnostic overhead. Compare
against a non-diagnostic baseline; don't regard this as a performance fix.

Host check: `python3 scripts/tests/test_live_pc.py`. It tests bounded capture,
mode/PC/LR, overrun and concurrent ring reads, generation changes and partial
Mach-O reads with mocked memory;
it does not validate actual XNU ABI, IRQ safety, sampling bias, or hardware.

## PID 1 用の固定カーネルを使うブートビルダー

`scripts/a04br3-iostat-stack-new-kernel.py` の既存入力を指定して
`--all-task --live-pc` を付ける。`--live-pc` は単独では使えず、
`--all-task` の引数に `a04br3-live-pc=1` だけを追加する。両指定がない
モードには追加しない。既存の同名引数、重複モード指定、stream・meter
との併用、1023 バイト超を拒否する。既存の seed、UUID、sidecar、ADT、
payload と boot の照合はそのまま実行する。manifest に `live_pc_pid1` を
記録し、カーネルは機種ソース `f2dee958a180ae6fee7b84a44baf9d74dcd22aff`、
UniXNU ソース `22146ccf1721db40f53e35f1afb1d745819f4735` の記録に加え、
既存カーネル SHA-256 `17904eca68b4c7e5f99350f4ddc57a671efc0357a93c984db9bb7f799336a4d1`
を要求する。ビルダーの `--source` は payload 生成用の別の固定版であり、
カーネルのソース版とは区別する。

固定 UniXNU ソースの `patches/xnu/zz-p31-irq-pc-stats.patch` は、
`XNU_IRQ_PC_STATS` が有効な ARM64 timer の割り込み経路から
`native_live_pc_sample` を呼ぶ。固定機種ソースは boot 引数が 1 のとき
PID 1 に設定する。該当カーネルの記録されたビルドスクリプトは
`XNU_IRQ_PC_STATS=1` を指定している。これは記録とソースに基づく証拠であり、
バイナリの完全なソース再現や実機でのフック動作の証明ではない。

ホスト検証は `python3 scripts/tests/test_iostat_stack_new_kernel.py`。
引数、モード衝突、長さ境界、既存カーネルの pin、UUID・ADT の拒否を試験する。
固定機種ソースの `test_live_pc.py` はモックのリング・画像読取りを試験し、
固定 UniXNU ソースの `check_irq_pc_hook.py` は XNU へのパッチ適用確認と
実際の追加ブロックの有効・無効、PC/LR、弱参照、ビルドフラグ伝播をホストで検証する。
これらは実際の boot image 生成、XNU 全体のリンク、実機の IRQ 動作を検証しない。
