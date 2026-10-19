"""Direct USB <-> SD file transfers on the Rescue channel (ops 80/81, Rescue feature 4).

Like DBI and nxdumptool, the console moves raw bytes in large transfers straight between USB and the SD card;
the Worker and its TCP bridge are not involved. Uploads go through a journaled transaction on the console that
checks SHA-256 before replacing the target; downloads are checked against the SHA-256 the console sends first."""
import hashlib
import json
import struct
import time
from pathlib import Path

from . import paths

LIMIT = 0xFFFFFFFF
BACKUP_LIMIT = 256 * 1024 * 1024
# The console hashes the whole file before it answers a read (op 81) and allocates the new file and hashes the
# old one before it answers a write (op 80); SD runs at >=10 MB/s, so allow 1 ms per 10 kB on top of a base.
GET_REPLY_MS = 600_000
PUT_REPLY_BASE_MS = 30_000


class HashingSink:
    def __init__(self, sink, digest):
        self.sink, self.digest = sink, digest

    def write(self, data):
        self.digest.update(data)
        self.sink.write(data)


def file_sha256(path):
    digest = hashlib.sha256()

    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)

    return digest


def get(usb, remote, local):
    payload = usb.stream_command(81, remote.encode(), GET_REPLY_MS)
    size, = struct.unpack_from('<Q', payload, 8)
    expected = payload[16:48].hex()
    digest = hashlib.sha256()
    Path(local).parent.mkdir(parents=True, exist_ok=True)

    with open(local, 'wb') as sink:
        usb.read_stream(HashingSink(sink, digest), size)

    if digest.hexdigest() != expected:
        raise RuntimeError(f'Bulk read of {remote}: SHA-256 mismatch (console {expected}, got {digest.hexdigest()})')

    return {'remote': remote, 'local': str(local), 'bytes': size, 'sha256': expected, 'via': 'bulk'}


def put(usb, remote, local, old):
    """old is None for a new file, else (size, sha256 bytes) of the current file."""
    local = Path(local)
    size = local.stat().st_size

    if size > LIMIT:
        raise ValueError('Maximum file size is 4 GiB - 1 (FAT32)')

    digest = file_sha256(local).digest()
    run = paths.ensure(paths.journal_dir() / f'bulk-change-{time.time_ns()}')
    record = {'target': remote, 'created': old is None, 'bytes': size, 'after_sha256': digest.hex(),
              'via': 'bulk', 'status': 'prepared'}

    if old is not None:
        record['before_sha256'] = old[1].hex()

        if old[0] <= BACKUP_LIMIT:
            get(usb, remote, run / 'before.bin')
        else:
            record['before_skipped'] = f'{old[0]} bytes is above the backup limit'

    save(run, record)
    usb.stream_command(80, struct.pack('<BQ', old is not None, size) + (old[1] if old else bytes(32)) + digest +
                       remote.encode(), PUT_REPLY_BASE_MS + (size + (old[0] if old else 0)) // 10_000)
    record['status'] = 'streaming'
    save(run, record)

    with local.open('rb') as source:
        usb.write_stream(source, size)

    error = usb.read_frame(120000 + size // 1000)

    if error:
        record['status'] = f'console error 0x{error:x}'
        save(run, record)

        raise RuntimeError(f'Rescue command 80: console error 0x{error:x}')

    record['status'] = 'committed'
    save(run, record)

    return {'journal': str(run), **record}


def save(run, record):
    (run / 'manifest.json').write_text(json.dumps(record, indent=2))
