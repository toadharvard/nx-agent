"""Install an nx-agent SD bundle (release zip or directory) onto a mounted Switch SD card.

Dry-run by default. Never edits [config] or autoboot in hekate_ipl.ini, never overwrites a foreign
sysmodule that uses the same title IDs, and backs up every replaced file.
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from .. import disks, paths
from ..common import OpError
from .hekate_ini import add_remote_entry

MANIFEST = 'nx-agent-manifest.json'
OWN_TIDS = ('420000000000D06B', '420000000000D06C')
USB_CONFLICTS = {'430000000000000B': 'sys-botbase (conflicts with usb:ds when its USB mode is enabled)'}
HEKATE_INI = 'bootloader/hekate_ipl.ini'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def open_bundle(source):
    source = Path(source)

    if source.is_dir():
        return source, None

    if not zipfile.is_zipfile(source):
        raise OpError('invalid', f'{source} is neither a directory nor a zip bundle')

    tmp = Path(tempfile.mkdtemp(prefix='nx-agent-bundle-'))

    with zipfile.ZipFile(source) as archive:
        for member in archive.namelist():
            if member.startswith('/') or '..' in Path(member).parts:
                raise OpError('invalid', f'Unsafe path in bundle: {member}')

        archive.extractall(tmp)

    return tmp, tmp


def _bundle_files(bundle):
    manifest = json.loads((bundle / MANIFEST).read_text())
    files = []

    for rel, sha in sorted(manifest['files'].items()):
        local = bundle / rel

        if _sha(local) != sha:
            raise OpError('invalid', f'Bundle file {rel} does not match its manifest hash')

        files.append((rel, local, sha))

    return manifest, files


def _check_root(root):
    if not disks.is_switch_sd(root):
        raise OpError('invalid', f'{root} does not look like a Switch SD (needs {", ".join(disks.SD_MARKERS)})')

    for marker in ('atmosphere', 'bootloader', 'config'):
        if (Path(root) / marker).is_symlink():
            raise OpError('invalid', f'{marker} is a symlink; refusing')


def _conflicts(root, manifest, files, force):
    known = set(manifest.get('known_hashes', [])) | {sha for _, _, sha in files}
    problems, warnings = [], []

    for tid in OWN_TIDS:
        exefs = Path(root) / 'atmosphere' / 'contents' / tid / 'exefs.nsp'

        if exefs.exists() and _sha(exefs) not in known:
            problems.append(f'{tid} already holds an unknown sysmodule ({_sha(exefs)[:12]})')

    for tid, note in USB_CONFLICTS.items():
        if (Path(root) / 'atmosphere' / 'contents' / tid / 'flags' / 'boot2.flag').exists():
            warnings.append(f'{tid}: {note}')

    if problems and not force:
        raise OpError('invalid', 'Title ID conflict: ' + '; '.join(problems) + ' (use force only if you know they are '
                      'older nx-agent builds)')

    return problems, warnings


def _atomic_copy(local, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.nxagent-tmp')
    shutil.copyfile(local, tmp)

    with open(tmp, 'rb+') as stream:
        os.fsync(stream.fileno())

    tmp.replace(target)


def install(root, source, apply=False, force=False):
    root = Path(root)
    _check_root(root)
    bundle, cleanup = open_bundle(source)

    try:
        manifest, files = _bundle_files(bundle)
        problems, warnings = _conflicts(root, manifest, files, force)
        ini = root / HEKATE_INI
        ini_text = ini.read_bytes().decode('utf-8')
        new_ini, ini_change = add_remote_entry(ini_text)
        plan = [{'path': rel, 'action': 'replace' if (root / rel).exists() and _sha(root / rel) != sha
                 else 'keep' if (root / rel).exists() else 'create'} for rel, _, sha in files]
        result = {'version': manifest.get('version'), 'root': str(root), 'files': plan, 'hekate_ipl': ini_change,
                  'forced_conflicts': problems, 'warnings': warnings, 'applied': False}

        if not apply:
            return result

        backup = paths.state_dir() / 'backups' / f'sd-install-{time.strftime("%Y%m%d-%H%M%S")}'
        backup.mkdir(parents=True)

        for (rel, local, sha), item in zip(files, plan):
            target = root / rel

            if item['action'] == 'keep':
                continue

            if target.exists():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / rel)

            _atomic_copy(local, target)

            if _sha(target) != sha:
                raise OpError('device_error', f'Readback mismatch for {rel}')

        if ini_change['changed']:
            (backup / HEKATE_INI).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ini, backup / HEKATE_INI)
            tmp = ini.with_name(ini.name + '.nxagent-tmp')
            tmp.write_bytes(new_ini.encode('utf-8'))
            tmp.replace(ini)

        result.update(applied=True, backup=str(backup),
                      appledouble_removed=disks.strip_appledouble(root) if os.uname().sysname == 'Darwin' else 0)

        return result
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)
