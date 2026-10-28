# Memory benchmark against the iostat4 reference

This opt-in comparison recipe preserves the exact iostat4 Data seed and every
GPU addon entry. It requires the three pinned historical reference artifacts
under `$RUNNER_TEMP/a04br3-iostat4-baseline/` named `scratch-data.img`,
`kernel.macho.gpuaddon`, and `kernel.macho.trustcache`. Their required SHA-256
values are in `a04br3-memory-matched-inputs.py`. The paths are filled by the
build orchestrator; none may be substituted silently.

Set `XNU_OWNER_DATA_VOLUME_UUID=CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36` and
`XNU_OWNER_CACHE_VOLUME_UUID=8846FE78-2910-4297-AF97-D59BB6F8FC16`. With the
pinned UniXNU and device sources, run `a04br3-memory-matched-prepare.sh`, the
normal UniXNU kernel build, then `a04br3-memory-matched-payload.sh` from the
UniXNU root. The prepare script builds and signs the benchmark, verifies its
signature and CDHash, validates the three original hashes, then constructs a
new GPU addon and owner trust bundle. The original six GPU file entries are
retained byte-for-byte and in their original modes and order. Only the
benchmark entry is added; the addon hash necessarily changes. The original
trust modules remain byte-for-byte and one benchmark trust module is added.
Neither operation edits the Data seed. The payload builder does not use
`--skip-setup` and verifies seed, kernel UUID string, addon, trust, payload,
VM compressor, Data and Cache UUID build settings and the exact original
arguments followed only by `mb=1`. The generated kernel must contain each
expected UUID string exactly once; this is a static integrity check, not proof
of the runtime mount selection.

`mb=1` is a compact device-specific opt-in because the reference command line
has only five bytes of headroom. The resulting 1023-byte line reaches the
DarwinBoot maximum; no other arguments or confirmation suffixes fit. The
existing long `a04br3-membench=1` option remains available in other profiles.
The pinned XNU source was searched for a direct `PE_parse_boot_arg*` use of
`mb` without a match; that search does not rule out opaque binary consumers.

The identical seed and UUID permit the existing persistent overlay to match,
but the addon installer can write the new benchmark file to persistent Data.
Check the real runtime log and preserve any needed Data before a test. Same
seed bytes do not by themselves prove the actual attached store's identity or
successful overlay recovery. Measuring adds work and diagnostic output.

This is a reproducible comparison **from pinned historical input artifacts**;
those historical GPU and seed assets have not been proven reproducible by a
clean official source-only build. The composed addon/trust and kernel differ
from iostat4 as described; all other fixed inputs must be independently checked.
Hardware operation and performance are not established by a successful build.
