#!/usr/bin/env bash
# Wake or leave an app through the existing NativeHidInput Home mailbox.
# Requires a kernel with A04BR3!HOME support and a04br3-autoreset enabled.
# The UART collector remains the only reader. No reset or flash is performed.
set -euo pipefail
UART=${UART:-/dev/ttyUSB0}
stty -F "$UART" 921600 cs8 -cstopb -parenb raw -echo clocal
printf '\nA04BR3!HOME\n' > "$UART"
