# Read-batch iostat UART observer (experimental)

This host-only observer reads an existing append-only regular UART log and
parent-written flash records. It does not access a serial device, flash, reset,
or operate the target. Keep the existing UART capture running. This observer
has not yet received its required independent review; inspect its results as
provisional and do not use them to decide hardware safety or performance.

Make a fresh absolute-path plan JSON with `schema: 1`, `manifest` and its
`manifest_sha256`, an absolute `uart` path, and a `runs` array of two objects.
Each run has a unique `id`, `variant` (`baseline` or `batch`), absolute `image`
and `flash_record` paths, and optional `conditions`. Image digests and profiles
are checked against the manifest, which is a declaration, not proof of build
provenance. The two entries can be in either order. Use a fresh plan and output
folder for every pair; repeated alternating pairs require separate plans.

Before each parent-operated flash, run `collect-read-batch-uart.py --uart-log
UART --checkpoint --out CHECKPOINT`. After independently confirming successful
flash and before reboot, the parent atomically publishes the corresponding JSON
record: `uart_offset` from the checkpoint, `uart_checkpoint` absolute path,
`flash_completed_uart_offset` equal to the current UART file size,
`boot_sha256`, `flash_success: true`, and a nonempty
`flash_success_evidence` identifying the saved flash log and its digest. These
are parent attestations, not proof of the flash. If the first preloader has
already appeared before the completed offset, the run is excluded. Never
publish a success record for a failed or uncertain flash.

Run `python3 scripts/observe-read-batch-iostat.py --plan PLAN --out OUT --watch
--poll-seconds 60 --timeout-seconds 21600`. It waits for both records, saves an
open snapshot after two complete sample end markers, and finalizes the first
boot after a subsequent preloader and boot reason. It records transitions in
`events.jsonl`, current state in `state.json`, capture and JSONL deltas per run,
and `comparison.json` once both runs finish or fail. The timeout retains any
partial snapshot; rerun the same command to resume. Existing captures are never
overwritten. An interrupted partial capture may require a fresh output folder;
retain the old one as evidence. A later watchdog reason, panic/EL3 candidate,
missing boundary or malformed frames is explicitly flagged. If no subsequent
boot is observed, the first boot remains open and no final result is claimed.

Counters cover global device transfer calls, not signature preparation or an
individual process. Samples are non-atomic and exclude unsampled start/end
intervals; continuity of sequence numbers does not prove UART completeness.
Cache, persistent Data, power/GPIO, GPU/CPU activity, actual workload, and
intentional versus fault-triggered reboot require independent evidence. This
profile lacks native root timing and cannot establish bat/read-byte matching or
signature-validation performance. The pair has differing ADT random seeds;
two runs are exploratory, not a controlled three-pair performance comparison.
The older rows-stack and CPU images differ in components and conditions and
must not be treated as equivalent baselines.
