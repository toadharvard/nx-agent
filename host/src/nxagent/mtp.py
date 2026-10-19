"""SD card access over DBI MTP (057e:201d) through the native mtp_fs helper.

Every mutation is verified with read-only listings or a SHA-256 readback. Overwrites go through a
verified temporary file and renames; replaced and deleted files are copied to the host journal first."""
import hashlib
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import paths
from .common import OpError

ROOT = 0xFFFFFFFF
SD_STORAGE = re.compile(r'^\d+: SD Card$')
FORBIDDEN_TOP = frozenset({'nintendo', 'emummc'})
BACKUP_LIMIT = 256 * 1024 * 1024
OWNER = re.compile(r'"UsbExclusiveOwner" = "pid (\d+), ([^"]+)"')


def split_path(path):
    text = str(path)

    if text.startswith('sdmc:'):
        text = text[5:]

    parts = [part for part in text.split('/') if part]

    for part in parts:
        if part in ('.', '..') or any(c in part for c in '\t\r\n\\'):
            raise OpError('invalid', f'Bad path component {part!r} in {path!r}')

    if parts and parts[0].lower() in FORBIDDEN_TOP or any(p.lower().endswith('.keys') for p in parts):
        raise OpError('invalid', f'{path}: keys, Nintendo and emuMMC paths are never accessed')

    return parts


def display(parts):
    return 'sdmc:/' + '/'.join(parts)


def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)

    return digest.hexdigest()


def mtp_interface_owner():
    if sys.platform != 'darwin':
        return None

    try:
        text = subprocess.run(['ioreg', '-l', '-w0', '-r', '-c', 'IOUSBHostInterface'], capture_output=True,
                              text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None

    for block in text.split('+-o '):
        if '"idVendor" = 1406' in block and '"idProduct" = 8221' in block:
            match = OWNER.search(block)

            if match:
                return int(match.group(1)), match.group(2)

    return None


def free_interface():
    owner = mtp_interface_owner()

    if owner is None or owner[0] == os.getpid():
        return None

    if owner[1] != 'ptpcamerad':
        raise OpError('busy', f'MTP interface is held by pid {owner[0]} ({owner[1]})', owner=owner[1])

    subprocess.run(['pkill', '-9', '-x', 'ptpcamerad'], capture_output=True)
    time.sleep(0.3)

    return owner


class Session:
    def __init__(self, binary=None):
        free_interface()
        self.process = subprocess.Popen([str(binary or paths.tool_path('mtp_fs'))], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buffer = b''
        self.storage = None

        try:
            ready = self._reply(30)
        except OpError:
            self.close()

            raise OpError('absent', 'DBI MTP did not answer; start DBI "Run MTP responder" on the console')

        if not ready.get('ok'):
            self.close()

            raise OpError('absent', ready.get('error', 'DBI MTP device not available'))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        process = self.process

        try:
            process.stdin.close()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    def _reply(self, timeout):
        fd = self.process.stdout.fileno()
        deadline = time.monotonic() + timeout

        while b'\n' not in self.buffer:
            left = deadline - time.monotonic()

            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise OpError('uncertain', 'mtp_fs reply timed out')

            chunk = os.read(fd, 65536)

            if not chunk:
                raise OpError('uncertain', 'mtp_fs exited before replying')

            self.buffer += chunk

        line, self.buffer = self.buffer.split(b'\n', 1)

        return json.loads(line)

    def call(self, *fields, timeout=60):
        try:
            self.process.stdin.write(('\t'.join(str(f) for f in fields) + '\n').encode())
            self.process.stdin.flush()
        except OSError as error:
            raise OpError('uncertain', f'mtp_fs pipe failed: {error}')

        reply = self._reply(timeout)

        if not reply.get('ok'):
            stack = '; '.join(reply.get('stack') or [])

            raise OpError('device_error', f"MTP {reply.get('error')}" + (f': {stack}' if stack else ''),
                          request=fields[0])

        return reply

    def sd_storage(self):
        if self.storage is None:
            storages = self.call('S')['storages']
            match = [s for s in storages if SD_STORAGE.match(s['description'] or '')]

            if len(match) != 1:
                raise OpError('device_error', 'DBI MTP does not expose exactly one "SD Card" storage',
                              storages=[s['description'] for s in storages])

            self.storage = match[0]['id']

        return self.storage

    def children(self, parent):
        return self.call('L', self.sd_storage(), parent)['entries']

    def child(self, parent, name):
        found = [e for e in self.children(parent) if e['name'] == name]

        if len(found) > 1:
            raise OpError('device_error', f'Duplicate entries named {name!r}')

        return found[0] if found else None

    def resolve(self, parts):
        node = {'id': ROOT, 'folder': True, 'name': '', 'size': 0}

        for part in parts:
            if not node['folder']:
                return None

            node = self.child(node['id'], part)

            if node is None:
                return None

        return node

    def folder(self, parts):
        node = self.resolve(parts)

        if node is None or not node['folder']:
            raise OpError('invalid', f'{display(parts)} is not an existing directory')

        return node

    def download(self, node, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        self.call('G', node['id'], str(Path(local).resolve()), timeout=transfer_timeout(node['size']))

        return sha256_file(local)


def transfer_timeout(size):
    return 60 + size / (1 << 20)


class MtpFiles:
    def __init__(self, binary=None, session=None):
        self.binary = binary
        self.factory = session

    def session(self):
        return self.factory() if self.factory else Session(self.binary)

    def ls(self, remote):
        with self.session() as s:
            node = s.folder(split_path(remote))

            return [{'name': e['name'], 'directory': e['folder'], 'size': e['size']} for e in s.children(node['id'])]

    def get(self, remote, local):
        parts = split_path(remote)

        with self.session() as s:
            node = s.resolve(parts)

            if node is None or node['folder']:
                raise OpError('invalid', f'{remote} is not an existing file')

            sha = s.download(node, local)

        return {'remote': display(parts), 'local': str(local), 'bytes': Path(local).stat().st_size, 'sha256': sha,
                'via': 'mtp'}

    def read_bytes(self, remote):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'file'
            self.get(remote, local)

            return local.read_bytes()

    def put(self, remote, local, create=False):
        parts = split_path(remote)

        if not parts:
            raise OpError('invalid', 'Cannot write the SD root')

        local = Path(local)
        sha = sha256_file(local)
        size = local.stat().st_size

        with self.session() as s:
            parent = s.folder(parts[:-1])
            current = s.child(parent['id'], parts[-1])

            if current is not None and current['folder']:
                raise OpError('invalid', f'{remote} is a directory')

            if current is None and not create:
                raise OpError('invalid', f'{remote} does not exist; pass create to make a new file')

            journal = self._journal('put', parts, s, current, local_sha=sha)

            if current is None:
                s.call('P', s.sd_storage(), parent['id'], parts[-1], str(local.resolve()),
                       timeout=transfer_timeout(size))
            else:
                self._replace(s, parent, current, parts, local, sha, size)

            self._verify_file(s, parts, sha)

        return {'remote': display(parts), 'bytes': size, 'sha256': sha, 'via': 'mtp', 'journal': journal,
                'replaced': current is not None}

    def write_small(self, remote, data):
        parts = split_path(remote)

        with self.session() as s, tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'data'
            local.write_bytes(data)
            parent = s.folder(parts[:-1])
            current = s.child(parent['id'], parts[-1])

            if current is None:
                s.call('P', s.sd_storage(), parent['id'], parts[-1], str(local))
            else:
                s.call('W', current['id'], str(local))

    def _replace(self, s, parent, current, parts, local, sha, size):
        stamp = time.time_ns()
        name = parts[-1]
        temp_name, old_name = f'.{name}.nxagent-new-{stamp}', f'.{name}.nxagent-old-{stamp}'
        temp_id = s.call('P', s.sd_storage(), parent['id'], temp_name, str(local.resolve()),
                         timeout=transfer_timeout(size))['id']
        self._verify_file(s, parts[:-1] + [temp_name], sha)

        try:
            s.call('R', current['id'], old_name)
        except OpError:
            s.call('D', temp_id)
            s.call('D', current['id'])
            s.call('P', s.sd_storage(), parent['id'], name, str(local.resolve()), timeout=transfer_timeout(size))

            return

        try:
            s.call('R', temp_id, name)
        except OpError:
            s.call('R', current['id'], name)
            s.call('D', temp_id)

            raise

        s.call('D', current['id'])

    def _verify_file(self, s, parts, sha):
        node = s.resolve(parts)

        if node is None or node['folder']:
            raise OpError('uncertain', f'{display(parts)} missing after write')

        with tempfile.TemporaryDirectory() as tmp:
            got = s.download(node, Path(tmp) / 'readback')

        if got != sha:
            raise OpError('device_error', f'{display(parts)} readback mismatch', expected=sha, got=got)

    def mkdir(self, remote, parents=False):
        parts = split_path(remote)

        if not parts:
            raise OpError('invalid', 'The SD root already exists')

        created = []

        with self.session() as s:
            node = s.resolve([])

            for index, part in enumerate(parts):
                child = s.child(node['id'], part)

                if child is not None and not child['folder']:
                    raise OpError('invalid', f'{display(parts[:index + 1])} is a file')

                if child is None:
                    if index < len(parts) - 1 and not parents:
                        raise OpError('invalid', f'{display(parts[:index + 1])} does not exist; pass parents')

                    s.call('M', s.sd_storage(), node['id'], part)
                    child = s.child(node['id'], part)

                    if child is None or not child['folder']:
                        raise OpError('uncertain', f'{display(parts[:index + 1])} missing after create')

                    created.append(display(parts[:index + 1]))

                node = child

        return {'remote': display(parts), 'created': created, 'via': 'mtp'}

    def rm(self, remote, recursive=False):
        parts = split_path(remote)

        if not parts:
            raise OpError('invalid', 'Refusing to delete the SD root')

        with self.session() as s:
            node = s.resolve(parts)

            if node is None:
                raise OpError('invalid', f'{remote} does not exist')

            if node['folder'] and s.children(node['id']) and not recursive:
                raise OpError('invalid', f'{remote} is a non-empty directory; pass recursive')

            journal = self._journal('rm', parts, s, None if node['folder'] else node)
            s.call('D', node['id'], timeout=600)

            if s.resolve(parts) is not None:
                raise OpError('uncertain', f'{remote} still present after delete')

        return {'remote': display(parts), 'deleted': True, 'directory': node['folder'], 'journal': journal,
                'via': 'mtp'}

    def mv(self, source, dest):
        src, dst = split_path(source), split_path(dest)

        if not src or not dst:
            raise OpError('invalid', 'Cannot move the SD root')

        with self.session() as s:
            node = s.resolve(src)

            if node is None:
                raise OpError('invalid', f'{source} does not exist')

            target = s.resolve(dst)

            if target is not None and target['folder']:
                dst = dst + [src[-1]]
                target = s.resolve(dst)

            if target is not None:
                raise OpError('invalid', f'{display(dst)} already exists')

            if dst[:len(src)] == src:
                raise OpError('invalid', 'Cannot move a directory into itself')

            parent = s.folder(dst[:-1])
            journal = self._journal('mv', src, s, None, dest=display(dst))

            if parent['id'] != node['parent'] and not (parent['id'] == ROOT and node['parent'] in (0, ROOT)):
                s.call('V', node['id'], s.sd_storage(), parent['id'], timeout=600)

            if dst[-1] != src[-1]:
                s.call('R', node['id'], dst[-1])

            moved = s.resolve(dst)

            if moved is None or s.resolve(src) is not None:
                raise OpError('uncertain', f'Move {display(src)} -> {display(dst)} not confirmed')

        return {'source': display(src), 'dest': display(dst), 'journal': journal, 'via': 'mtp'}

    def _journal(self, op, parts, s, node, **extra):
        run = paths.ensure(paths.journal_dir() / f'mtp-change-{time.time_ns()}')
        manifest = {'op': op, 'target': display(parts), **extra}

        if node is not None:
            if node['size'] <= BACKUP_LIMIT:
                manifest['original_sha256'] = s.download(node, run / 'original')
            else:
                manifest['original_skipped'] = f"{node['size']} bytes is above the backup limit"

        (run / 'manifest.json').write_text(json.dumps(manifest, indent=2))

        return str(run)


class MtpMailbox:
    """Worker mailbox on the SD card, used while DBI MTP replaces the sidecar USB interface."""
    STATUS = 'sdmc:/config/usb-worker-mailbox/status.json'
    REQUEST = 'sdmc:/config/usb-worker-mailbox/request.bin'

    def __init__(self, files):
        self.files = files

    def read_status(self):
        try:
            return json.loads(self.files.read_bytes(self.STATUS))
        except (OpError, ValueError) as error:
            raise OpError('absent', f'Mailbox status is not readable over MTP: {error}')

    def submit(self, request):
        try:
            self.files.write_small(self.REQUEST, request)
        except OpError:
            return 6

        return 0
