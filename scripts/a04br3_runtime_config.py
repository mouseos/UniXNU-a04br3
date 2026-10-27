"""Gate the device-specific PMU experiment on the actual XNU build config."""
from pathlib import Path


def require_cpu_counters_disabled(config: Path) -> None:
    if not config.is_file() or config.read_text().strip() != '#define CONFIG_CPU_COUNTERS 0':
        raise ValueError('a04br3 runtime sampling requires CONFIG_CPU_COUNTERS=0')
