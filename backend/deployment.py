"""Shared runtime settings for Studio, local runs and older hosted installs."""
import os
from pathlib import Path
import re


def hosted():
    return os.getenv("APP_ENV") == "production" or bool(os.getenv("RENDER"))


def agent_capacity():
    return int(os.getenv("AGENT_MAX_ACTIVE_TASKS") or (1 if hosted() else 2))


def archive_on_volume(directory, mountinfo=Path('/proc/self/mountinfo')):
    """Check the closest mount, including Linux bind mounts on the root filesystem."""
    target = Path(directory).resolve()
    try:
        mounts = mountinfo.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return any(path != Path(path.anchor) and path.is_mount() for path in (target, *target.parents))
    matching = {}
    for line in mounts:
        fields, separator, details = line.partition(' - ')
        parts = fields.split()
        if separator and len(parts) >= 5 and details.split():
            point = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), parts[4]))
            if point == target or point in target.parents:
                matching[point] = details.split()[0]
    if not matching:
        return False
    closest = max(matching, key=lambda point: len(point.parts))
    return closest != Path(closest.anchor) and matching[closest] not in {'tmpfs','ramfs','overlay','proc','sysfs','devtmpfs'}
