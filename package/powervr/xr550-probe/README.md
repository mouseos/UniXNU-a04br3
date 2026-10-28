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
pass the generated `trust.json` as `--extra-trust` to the normal owner trust
bundle builder. Pass `--xr550-probe /path/to/output` to
`a04br3-seed-powervr-probe.py`: in this opt-in diagnostic image it replaces
`bin/powervrprobed` and places the metallib alongside it. Use the existing
`a04br3-powervrprobed=1` boot hook, a published Metal GPU plugin with COLOR
ROA enabled, and authorize `powervrprobed` as a Metal client. This image runs
the XR probe in place of the ordinary PowerVR probe. Build and trust the normal
GPU plugin and Vulkan driver as well. Inspect serial output for the two PASS
lines; spawn, build or feature advertising alone are not success.
