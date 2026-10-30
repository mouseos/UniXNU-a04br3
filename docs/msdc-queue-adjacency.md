# NativeMsdc queue adjacency diagnostic

This is an opt-in diagnostic, not an I/O optimization. Select the existing
IRQ-wait, worker, verified block-device build, then set
`CONFIG_XNU_MSDC_QUEUE_ADJACENCY=y` in a diagnostic defconfig and boot with
`native-msdc-adjacency=1`. The standard defconfig remains off. The device
builder requires `XNU_MSDC_IRQ_WAIT=1`, `XNU_MSDC_IRQ_WORKER=1`,
`XNU_NATIVE_MSDC_BLOCK=1` and `--verified-root`. Source and image provenance
must include the UniXNU NativeMsdc gitlink and device commit. A build alone
is not evidence of target ABI, image correctness or device behavior.

Only verified immutable reads admitted to the worker queue are analyzed. See
NativeMsdc README for offer/queue/start/finish scope, request IDs, queue-tail
adjacency, timing bins, status, partial results, sample loss and privacy.
The 16 completion examples per report are biased; aggregate totals continue
when examples are dropped. The reporter changes scheduling and logging load.
Direct fallback and pre-admission rejections are not completion records.
No claim is made about physical-media overlap, fresh re-verification, speedup,
or the effect of combining requests.
