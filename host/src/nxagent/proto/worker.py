"""Worker client over USB interface 0. Each mutation is journaled on the host.

No input is sent merely by connecting; no automatic retries of mutations.
"""
import hashlib
import json
import struct
import time
from pathlib import Path
from .. import paths
from .transport import UsbSocket

ROOT = paths.state_dir()
BUTTONS = {name: 1 << i for i, name in enumerate([
    'A', 'B', 'X', 'Y', 'LSTICK', 'RSTICK', 'L', 'R', 'ZL', 'ZR',
    'PLUS', 'MINUS', 'LEFT', 'UP', 'RIGHT', 'DOWN'])}
BUTTONS.update(HOME=1 << 18, CAPTURE=1 << 19)
LOG_ROOTS = ['atmosphere/crash_reports', 'atmosphere/fatal_reports', 'atmosphere/erpt_reports', 'atmosphere/binlogs', 'config/4IFIR']

WRITE_LIMIT = 256 * 1024 * 1024

class Sidecar:
    @classmethod
    def drain_usb(cls):
        transport=UsbSocket(connect_gdb=False)
        try:return transport.drain_pending_replies()
        finally:transport.close()

    @classmethod
    def wait_for_usb(cls, seconds=180):
        """Wait for boot using only read-only probes; never replay mutations."""
        if not 1 <= seconds <= 600:
            raise ValueError('Startup wait must be 1..600 seconds')
        deadline=time.monotonic()+seconds;last=None
        while time.monotonic()<deadline:
            try:return cls()
            except Exception as e:
                last=e
                if 'Another sidecar/debugger client' in str(e) or 'installation required' in str(e):
                    raise
                if isinstance(e, OSError):
                    try:cls.drain_usb()
                    except Exception as recovery_error:last=recovery_error
                time.sleep(min(2,max(0,deadline-time.monotonic())))
        raise TimeoutError(f'Sidecar did not return within {seconds}s: {last}')

    def __init__(self):
        self.usb = UsbSocket(connect_gdb=False)
        try:
            version = struct.unpack('<4I', self.call(6))[0]
            if version < 4:
                raise RuntimeError(f'Console is running sidecar v{version}; v4 installation required')
            self.capabilities = struct.unpack('<6I', self.call(9))
        except BaseException:
            self.usb.close()
            raise

    def call(self, op, data=b''):
        error, result, _ = self.usb.command(op, data)
        if error:
            raise RuntimeError(f'Sidecar command {op}: console error 0x{error:x}')
        return result

    def close(self):
        self.usb.close()

    def app(self):
        rc, pid, title = struct.unpack('<IQQ', self.call(45))
        return {'result': f'0x{rc:x}', 'pid': pid, 'title_id': f'{title:016x}',
                'query_succeeded': rc == 0}

    def list(self, path):
        entries = []
        for index in range(10000):
            data = self.call(16, struct.pack('<I', index) + path.encode())
            if not data:
                return entries
            name = data[1:].decode()
            if '/' in name or '\\' in name or name in ('.', '..'):
                raise RuntimeError('Invalid remote entry')
            entries.append({'name': name, 'directory': bool(data[0])})
        raise RuntimeError('Directory limit reached')

    def get(self, path, limit=WRITE_LIMIT):
        result = bytearray()
        while True:
            chunk = self.call(17, struct.pack('<Q', len(result)) + path.encode())
            result.extend(chunk)
            if len(result) > limit:
                raise RuntimeError('Read exceeds configured limit')
            if len(chunk) < 4080:
                return bytes(result)

    def fingerprint(self, path):
        data = self.call(18, path.encode())
        return struct.unpack_from('<Q', data)[0], data[8:40]

    def rename(self, source, dest):
        self.call(36, source.encode() + b'\0' + dest.encode())

    def unlink(self, path):
        self.call(37, path.encode())

    def rmdir(self, path):
        self.call(38, path.encode())

    def mkdir(self, path):
        self.call(39, path.encode())

    def put(self, path, content, create=False):
        if len(content) > WRITE_LIMIT:
            raise ValueError('Maximum write is 256 MiB')
        # Missing file is explicit: never treat arbitrary read errors as absence.
        old = None if create else self.get(path, WRITE_LIMIT)
        digest = hashlib.sha256(content).digest()
        old_digest = bytes(32) if create else hashlib.sha256(old).digest()
        run = paths.journal_dir() / f'sidecar-change-{time.time_ns()}'
        run.mkdir(parents=True)
        if old is not None:
            (run / 'before.bin').write_bytes(old)
        (run / 'after.bin').write_bytes(content)
        record = {'target': path, 'created': create, 'before_sha256': old_digest.hex(),
                  'after_sha256': digest.hex(), 'status': 'prepared'}
        def save():
            (run / 'manifest.json').write_text(json.dumps(record, indent=2))
        save()
        try:
            self.call(32, struct.pack('<BQ', not create, len(content)) + old_digest + digest + path.encode())
            record['status'] = 'uploading'; save()
            for offset in range(0, len(content), 4072):
                self.call(33, struct.pack('<Q', offset) + content[offset:offset + 4072])
            self.call(34)
            size, remote_hash = self.fingerprint(path)
            if size != len(content) or remote_hash != digest:
                raise RuntimeError('Readback hash mismatch')
            record['status'] = 'installed_readback_verified'; save()
        except BaseException:
            record['status'] = 'uncertain_inspect_before_retry'; save()
            # Do not blindly abort/retry an uncertain commit after USB loss.
            raise
        return {'backup': str(run), **record}

    def rollback(self, backup):
        backup=Path(backup)
        record=json.loads((backup/'manifest.json').read_text())
        if record['created']:
            raise RuntimeError('Creation rollback requires removal; this client never deletes config files')
        old=(backup/'before.bin').read_bytes()
        if hashlib.sha256(old).hexdigest()!=record['before_sha256']:
            raise RuntimeError('Backup hash mismatch')
        _, current=self.fingerprint(record['target'])
        if current.hex()!=record['after_sha256']:
            raise RuntimeError('Target changed since this backup; refusing stale rollback')
        return self.put(record['target'],old)

    def screenshot(self, destination, layer=None):
        if layer is not None and (self.capabilities[0]<5 or layer not in (0,1,2,3,4,6)):
            raise ValueError('Layer selection needs sidecar v5 and a valid layer')
        size, = struct.unpack('<Q', self.call(43,b'' if layer is None else bytes([layer])))
        if not 0 < size <= 0x80000:
            raise RuntimeError('Invalid JPEG size')
        image = bytearray()
        while len(image) < size:
            chunk = self.call(44, struct.pack('<Q', len(image)))
            if not chunk or len(image) + len(chunk) > size:
                raise RuntimeError('Truncated or oversized JPEG response')
            image.extend(chunk)
        if not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
            raise RuntimeError('Invalid JPEG framing')
        destination = Path(destination); destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(image)
        return {'image': str(destination.resolve()), 'bytes': size}

    def press(self, names, duration=120, sticks=(0, 0, 0, 0)):
        if not 1 <= duration <= 1000:
            raise ValueError('Press duration must be 1..1000 ms')
        buttons = 0
        for name in names.split('+'):
            buttons |= BUTTONS[name.upper()]
        packet = struct.pack('<Q4iI', buttons, *sticks, duration)
        fallback = None
        try:
            self.call(41, packet)
        except RuntimeError as e:
            if '0x1c24ca' not in str(e):
                raise
            self.call(40, b'\1')
            time.sleep(1)
            self.call(41, packet)
            fallback = 'pro rejected with 0x1c24ca; switched to rail'
        time.sleep(duration / 1000 + .05)
        result = {'pressed': names, 'duration_ms': duration, 'controller': struct.unpack('<4I', self.call(48))}
        if fallback:
            result['fallback'] = fallback
        return result

    def collect(self, output):
        output = Path(output); output.mkdir(parents=True, exist_ok=True)
        manifest = []
        def walk(remote, relative, depth=0):
            if depth > 5:
                raise RuntimeError('Directory depth limit')
            for item in self.list(remote):
                name = item['name']; child = f'{remote}/{name}'; local = relative / name
                if item['directory']:
                    walk(child, local, depth + 1)
                else:
                    data = self.get(child); destination = output / local
                    destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
                    manifest.append({'path': str(local), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        for root in LOG_ROOTS:
            try:
                walk('sdmc:/' + root, Path(root))
            except Exception as e:
                manifest.append({'path': root, 'error': str(e)})
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        return {'output': str(output.resolve()), 'files': sum('sha256' in x for x in manifest),
                'errors': sum('error' in x for x in manifest)}
