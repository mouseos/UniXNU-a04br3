# XR550 framebuffer fetch probe (work in progress)

This standalone experimental Metal probe renders to format 550 and reads the
same pixel through `[[color(0)]]`. A second RGBA32Float attachment records the
fetched float4, including alpha, so the comparison does not use the format's
plane-0-only CPU readback. It uses two draws in one render pass, checks all
1024 pixels, and covers negative and above-one color and independent alpha.
The tolerance is 0.006 per component. This is not the CoreAnimation shader,
not a test of same-draw primitive ordering, and not full XR conformance.

Build on a macOS host with Xcode's iPhoneOS SDK and Metal toolchain:
`package/powervr/xr550-probe/build.sh /path/to/output`. The resulting binary
and metallib need platform-appropriate signing, trust and packaging before they
can run on the target. Invoke with the metallib path. This source is not yet
integrated into the board's addon or unattended boot; no device test is claimed.
