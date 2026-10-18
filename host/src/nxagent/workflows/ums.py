"""Offline SD edits: reboot into Remote Hekate, expose the SD over UMS, copy, verify, eject, boot HOS."""
import hashlib
import os
import shutil
import sys
from pathlib import Path

from .. import disks
from ..common import HOS, NYX, OpError

POST_HOOKS = {
    'clear_4ifir_alarm': 'config/4IFIR/alarm.flag',
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _safe_target(root, remote):
    rel = remote.removeprefix('sdmc:/').lstrip('/')

    if not rel or '..' in Path(rel).parts:
        raise OpError('invalid', f'Bad SD path {remote}')

    lowered = rel.lower()

    if any(part in lowered for part in ('.keys', 'nintendo/', 'emummc', 'saves/', 'automatic_backups')):
        raise OpError('invalid', f'Refusing to write protected SD path {remote}')

    return Path(root) / rel


def ums_put(daemon, files, hooks=(), step=lambda *a, **k: None, timeout=180):
    if not files:
        raise OpError('invalid', 'No files to copy')

    for item in files:
        if not Path(item['local']).is_file():
            raise OpError('invalid', f"Local file not found: {item['local']}")

    unknown = set(hooks) - set(POST_HOOKS)

    if unknown:
        raise OpError('invalid', f'Unknown hooks {sorted(unknown)}; known {sorted(POST_HOOKS)}')

    if daemon.monitor.state == 'hos':
        step('reboot_to_remote')
        daemon.reboot_to('remote', wait=True, timeout=timeout)
    elif daemon.monitor.state != 'hekate_remote':
        raise OpError('absent', f'Need HOS or Remote Hekate, state is {daemon.monitor.state}')

    before = disks.external_disks()
    step('hekate_ums')
    daemon.nyx_action('ums', wait=False)
    disk, root = None, None

    def found():
        nonlocal disk, root
        disk, root = disks.find_sd(disks.external_disks() - before, seconds=0)

        return root is not None

    if not daemon.wait_until(found, timeout, interval=2):
        raise OpError('absent', 'SD card did not mount over UMS')

    step('mounted', disk=disk, root=str(root))
    copied = []

    try:
        for item in files:
            target = _safe_target(root, item['remote'])
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + '.nxagent-tmp')
            shutil.copyfile(item['local'], tmp)

            with open(tmp, 'rb+') as stream:
                stream.flush()
                os.fsync(stream.fileno())

            tmp.replace(target)

            if _sha(target) != _sha(item['local']):
                raise OpError('device_error', f'Readback mismatch for {item["remote"]}')

            copied.append({'remote': item['remote'], 'sha256': _sha(target)})
            step('copied', remote=item['remote'])

        for hook in hooks:
            path = Path(root) / POST_HOOKS[hook]

            if path.exists():
                path.unlink()
                step('hook', name=hook, removed=str(path))

        if sys.platform == 'darwin':
            step('strip_appledouble', removed=disks.strip_appledouble(root))
    finally:
        step('eject', disk=disk)
        disks.eject(disk)

    if not daemon.wait_until(lambda: NYX in daemon.present(), 90):
        raise OpError('absent', 'Remote Hekate did not return after UMS; files were copied', copied=copied)

    step('launch_hos')
    daemon.nyx_action('launch', wait=True, timeout=timeout)

    if not daemon.wait_until(lambda: HOS in daemon.present(), timeout):
        raise OpError('absent', 'HOS did not return; files were copied', copied=copied)

    return {'copied': copied, 'state': daemon.monitor.state}
