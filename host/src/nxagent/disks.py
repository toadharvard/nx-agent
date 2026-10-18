"""Removable-disk helpers for the hekate UMS path on macOS (diskutil) and Linux (lsblk + udisksctl)."""
import json
import plistlib
import subprocess
import sys
import time
from pathlib import Path

SD_MARKERS = ('bootloader/hekate_ipl.ini', 'atmosphere')


def _run(args, timeout=30):
    return subprocess.run(args, capture_output=True, timeout=timeout)


def external_disks():
    if sys.platform == 'darwin':
        out = _run(['diskutil', 'list', '-plist', 'external'], 20)

        if out.returncode:
            return set()

        return set(plistlib.loads(out.stdout).get('WholeDisks', []))

    out = _run(['lsblk', '-J', '-d', '-o', 'NAME,TRAN,RM,TYPE'], 20)

    if out.returncode:
        return set()

    return {d['name'] for d in json.loads(out.stdout).get('blockdevices', [])
            if d.get('type') == 'disk' and (d.get('tran') == 'usb' or d.get('rm') in (True, '1', 1))}


def _mac_mounts(disk):
    out = _run(['diskutil', 'list', '-plist', disk])

    if out.returncode:
        return []

    mounts = []

    for entry in plistlib.loads(out.stdout).get('AllDisksAndPartitions', []):
        for part in entry.get('Partitions', []) + [entry]:
            if part.get('MountPoint'):
                mounts.append(Path(part['MountPoint']))

    return mounts


def _linux_parts(disk):
    out = _run(['lsblk', '-J', '-o', 'NAME,PATH,MOUNTPOINTS,FSTYPE', f'/dev/{disk}'])

    if out.returncode:
        return []

    parts = []

    for dev in json.loads(out.stdout).get('blockdevices', []):
        for child in dev.get('children', []) or [dev]:
            if child.get('fstype'):
                parts.append(child)

    return parts


def _linux_mounts(disk, mount=True):
    mounts = []

    for part in _linux_parts(disk):
        points = [p for p in part.get('mountpoints') or [] if p]

        if not points and mount:
            _run(['udisksctl', 'mount', '-b', part['path']], 60)
            points = [p for p in (_linux_parts_by_path(part['path']).get('mountpoints') or []) if p]

        mounts.extend(Path(p) for p in points)

    return mounts


def _linux_parts_by_path(path):
    out = _run(['lsblk', '-J', '-o', 'PATH,MOUNTPOINTS', path])

    if out.returncode:
        return {}

    devices = json.loads(out.stdout).get('blockdevices', [])

    return devices[0] if devices else {}


def mounts(disk):
    return _mac_mounts(disk) if sys.platform == 'darwin' else _linux_mounts(disk)


def is_switch_sd(root):
    return all((Path(root) / marker).exists() for marker in SD_MARKERS)


def find_sd(disks, seconds=60):
    deadline = time.monotonic() + seconds

    while True:
        for disk in sorted(disks):
            for mount in mounts(disk):
                if is_switch_sd(mount):
                    return disk, mount

        if time.monotonic() >= deadline:
            return None, None

        time.sleep(1)


def strip_appledouble(root, subdirs=('atmosphere', 'bootloader', 'config', 'switch')):
    removed = 0

    for sub in subdirs:
        base = Path(root) / sub

        if not base.exists():
            continue

        for path in base.rglob('._*'):
            if path.is_file():
                path.unlink()
                removed += 1

    return removed


def eject(disk, attempts=5):
    last = ''

    for _ in range(attempts):
        if sys.platform == 'darwin':
            out = _run(['diskutil', 'eject', disk], 60)
        else:
            for part in _linux_parts(disk):
                if any(part.get('mountpoints') or []):
                    _run(['udisksctl', 'unmount', '-b', part['path']], 60)

            out = _run(['udisksctl', 'power-off', '-b', f'/dev/{disk}'], 60)

        if out.returncode == 0:
            return True

        last = out.stderr.decode(errors='replace').strip()[-200:]
        time.sleep(5)

    raise RuntimeError(f'eject {disk} failed: {last}')
