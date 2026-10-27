# WiFi source provenance and notices

This directory is source code, not a device calibration or firmware dump. Build
integration and hardware qualification are separate from source provenance.

- `IMPORT.sources.json`: 107 original blobs from
  `mouseos/xnu_kernel_benesse_a04br3` commit
  `f5d43bf1677955e7e367545b9bbbd6449776cdda`. The original blob hashes were
  checked against that commit. 94 remain byte-identical; thirteen adaptations are
  recorded with both hashes in `PORT.sources.json`. They change boot firmware
  views, opt-in/service allocation and crypto/nonce binding for this port, plus
  AVB option enum typing, initialization/interface-down/input guard formatting and power/output enum typing and their Apple source
  provenance metadata.
- `network/apple/SOURCE.json`: original Apple IONetworkingFamily revision
  `bb910e2b458939f6b1f53c0fa6907f258b431ea4`, original and vendored hashes,
  build adaptations and source-local additions. The 33 file records and three
  additions match their recorded vendored hashes. Preserve every file's original
  copyright/license notice and `network/apple/APPLE_LICENSE` unchanged. Some
  historical file headers name APSL 1.1 while the upstream-supplied license file
  is APSL 2.0; this inventory does not relabel those headers or resolve their
  legal interpretation. New source-owned IO80211 classes do not claim Apple's
  private C++ ABI or user-client compatibility.
- `bearssl/UPSTREAM.json`: BearSSL revision
  `7bea48e5e850ab4cafbe68d3765cdaba13a86d6f`, MIT license in
  `bearssl/LICENSE.txt`, original Thomas Pornin notices retained. The copied
  subset, including its upstream metadata, is byte-identical to the pinned donor
  at `libkern/crypto/a04br3_bearssl`; each file's donor/current hash is recorded
  in `PORT.sources.json`. This is not an independent fetch of that upstream
  BearSSL commit.
- `host-fixture/SOURCE.json`: Apple XNU declarations at revision
  `d8b80295118ef25ac3a784134bcf95cd8e88109f`, plus three MIT host tests from the
  pinned donor. `host-fixture/LICENSE.port` is that donor's unmodified original
  copyright and license text for its port test sources. Apple declarations retain
  their own Apple source notices. The fixture is not linked into the board
  archive. Its relative symlinks point to this directory's `sdio`/`bearssl`
  sources; no external checkout is required by those links.
- `network/tests`: 19 donor tests, with original/current hashes recorded in
  `PORT.sources.json`; six have port-specific fixture/include adapters. They use
  generated synthetic packets, keys and MACs, not extracted production settings.

The firmware builder refers to public vendor commit
`0c756f413e7011231c8fb793af0930311023f68d` and checks three fixed SHA-256 inputs.
Those binaries are not vendored here. A public source URL alone is not a
redistribution-license determination; this inventory does not assert that the
firmware distribution terms have been independently cleared.

No per-device NVRAM, MAC, password, production nonce or calibration file is
included. Runtime calibration must come through the read-only named-partition
API. References to earlier hardware observations do not qualify this port's
SDK link, actual radio initialization, association or IP traffic.
