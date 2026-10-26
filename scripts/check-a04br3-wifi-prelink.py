#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Optional official archive preflight; no compilation, firmware download or hardware."""
import os
from pathlib import Path
from a04br3_wifi_build import guard_prelink

if __name__ == '__main__':
    try:
        report = guard_prelink(Path(__file__).resolve().parents[1], os.environ)
    except (ValueError, OSError) as error:
        raise SystemExit(f'WiFi prelink preflight refused: {error}')
    if report is not None:
        print(f"WiFi prelink preflight PASS: {len(report['effective_roots'])} roots, "
              f"{len(report['selected'])} original modules; source network families excluded")
