# XR550 framebuffer fetch probe (experimental)

The probe draws coordinate-dependent values to Metal format 550, ends the pass
with STORE, then LOADs it in a second pass and reads `[[color(0)]]`. Another
RGBA32Float attachment records the fetched RGB and alpha on the GPU. A host
reference independently rounds fragment output to half, encodes linear 10-bit
XR and 8-bit UNORM alpha, decodes, and rounds the tile value to half. The
pattern deliberately lies between quantization codes. Boundary cases exercise
color and alpha clamping. Every component of all 1024 pixels is compared;
readback begins as NaNs and the output attachment is cleared to a sentinel.
The command buffer has a 30 second deadline. Timeout exits the process without
freeing resources while GPU work might still be running.

This is a synthetic two-pass test, not CoreAnimation's shader or a test of
same-draw primitive ordering. The half tile model is from metal2vulkan's
extended_range.rs (EV XRR-rtp1-m3/m4); it must be checked against the target.
Do not treat a pass as general ROA or XR conformance.

Build on an Xcode host using `python3 scripts/build-a04br3-xr550-probe.py
--out /path/to/output`. Its ad-hoc 4K signature and entitlements are checked;
Use `make-a04br3-gpu-addon.py --xr550-probe /path/to/output` with the
normal signed probe, ROA-enabled Metal plugin and pinned original trust bundle.
It verifies the XR binary's signature and CDHash before packing, places the
probe in `bin/powervrprobed`, adds the metallib and publish/client markers,
and emits an owner trust bundle including the XR CDHash. The ordinary PowerVR
probe is replaced only for this diagnostic image. Use the existing
`a04br3-powervrprobed=1` boot hook and the generated addon and trust bundle.
Inspect serial output for both PASS lines; spawn, build or feature advertising
alone are not success.
