"""Container-wide memory diagnostics (includes Harness subprocesses)."""
import logging
from pathlib import Path

CGROUP = Path('/sys/fs/cgroup')

def memory_readings():
    values = {}
    for name, filename in [('used', 'memory.current'), ('peak', 'memory.peak'), ('limit', 'memory.max')]:
        try:
            raw = (CGROUP / filename).read_text().strip()
            if raw.isdigit():
                values[name] = int(raw)
        except OSError:
            pass
    return values


def low_headroom(values):
    limit = values.get('limit')
    if not limit or 'used' not in values:
        return False
    reserve = min(limit // 16, 32 * 1024 * 1024)
    if values['used'] < limit - reserve:
        return False
    # File pages already on the inactive list are reclaimable under pressure.
    try:
        stats = dict(line.split() for line in (CGROUP / 'memory.stat').read_text().splitlines())
        inactive = int(stats.get('inactive_file', 0))
    except (ValueError, OSError):
        inactive = 0
    return values['used'] - inactive >= limit - reserve


def log_memory(stage, readings=None):
    values = memory_readings() if readings is None else readings
    if values:
        mib = {key + '_mib': round(value / 1048576, 1) for key, value in values.items()}
        logging.getLogger('uvicorn.error').info('Container memory: stage=%s %s', stage, mib)
