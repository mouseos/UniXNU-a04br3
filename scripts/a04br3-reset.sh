#!/usr/bin/env bash
# Ask a running a04br3 kernel to reset into LK fastboot now (package/A04BR3AutoReset.cpp, a04br3-autoreset=<seconds>):
# writes the request line to UART0. A collector reading the same port may stay open.
# usage: [UART=/dev/ttyUSB0] scripts/a04br3-reset.sh
set -eu
UART=${UART:-/dev/ttyUSB0}
printf '\nA04BR3!RST\n' > "$UART"
