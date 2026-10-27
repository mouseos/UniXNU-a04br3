# SDK interface compile repairs

The complete 13266bd SDK diagnostic identified seven errors in the three Apple
translation units owned by this change. Other device diagnostics are integrated
from reviewed commits 0f839ae and c4e3dc1; no SDK runner edits are prerequisites.

- `IONetworkController.cpp`: remove bookkeeping whose only reader was an
  already-disabled link notification. Preserve property calls, success/failure
  returns, selected-name updates and medium-lock cleanup.
- `IONetworkInterface.cpp`: replace the incompatible power callback cast with a
  non-capturing lambda of the exact `IONetworkController::Action` type. Forward
  target, notice and inside-gate flag unchanged; the existing void handler and
  its declaration remain. Serialize kernel events with an aligned fixed prefix
  and family/unit/name payload. Preserve 48 bytes with either XNU event-data
  array declaration; initialize the ID and padding before prefix copy. Keep
  raw DLIL events, bandwidth updates and retain/thread-call logic unchanged.
- `IOOutputQueue.cpp`: adapt the four-argument statistics handler to the exact
  seven-argument action without changing the handler or its return value. Adapt
  the four-argument gated handler to the exact five-argument gate action and
  retain the cannot-lock/deferred behavior. Two volatile increments become
  explicit read-plus-one assignments under their original queue lock.

No public/private class header, member layout, vtable, original static handler
signature or normal warning flags change. The other casts in these files that
already match their callback types or restore OSMemberFunctionCast pointers are
retained; the captured SDK log did not identify them as errors.

```sh
python3 scripts/tests/test_a04br3_wifi_sdk_callbacks.py
# Optional: use the exact production event declaration from the fixed XNU Git object.
python3 scripts/tests/test_a04br3_wifi_sdk_callbacks.py --xnu-source /path/to/tree/refer/xnu
python3 scripts/tests/test_a04br3_wifi_provenance.py
python3 scripts/tests/test_a04br3_wifi_driver.py
python3 package/wifi/network/tests/test_bsd_network.py
```

The focused test extracts shipped methods/lambdas, assigns the actual imported
Action typedefs, and exercises stats return/arguments, synchronous power notices,
gate success/cannot-lock/disabled behavior, volatile wrap, and property failure.
The event test compares all 48 wire bytes and checks null input, link/speed/raw
DLIL cases and thread retain/release in both `event_data[1]` and `event_data[]`
configurations. Host ASan/UBSan and ARM64 syntax checks pass; old casts, nested
flexible-array struct, unused tracking and volatile increments reproduce their
compiler diagnostic classes. Local Clang names the cast diagnostic
`-Wcast-function-type-strict`; Apple SDK uses `-Wcast-function-type-mismatch`.

These tests are synthetic collaborator/byte-contract tests, not proof of full
XNU class ABI linkage or WiFi operation. Re-run the compile-all tool on the
integrated commit, then the ordinary archive/kernel/prelink build. Full per-file
SDK logs remain the source of truth for any additional errors.
