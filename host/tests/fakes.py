import collections
import hashlib
import struct
import tempfile
from pathlib import Path


import usb.core

from nxagent.common import HOS, MTP, NYX, EventLog, OpError
from nxagent.ops import Daemon

FB_SIZE = 1280 * 720 * 4


def usb_timeout():
    return usb.core.USBTimeoutError('Operation timed out', errno=60)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def advance(self, seconds):
        self.t += seconds


class FakeConsole:
    def __init__(self):
        self.present = {HOS}
        self.timeline = []
        self.on_reboot = []
        self.disk_timeline = []
        self.disks = set()
        self.worker_running = True
        self.worker_version, self.worker_features = 7, 63
        self.rescue_version, self.rescue_features, self.launch_ready = 7, 1, 1
        self.start_brings_up = True
        self.calls, self.attempts = [], []
        self.faults = {}
        self.files, self.dirs = {}, {}
        self.lease = self.pad_ready = self.wake_alarm = self.seq_active = 0
        self.commands = 10
        self.locked = set()
        self.drains, self.opens = collections.Counter(), collections.Counter()
        self.starts = 0
        self.guard = None
        self.screenshot_error = 0
        self.nyx_version = 2
        self.tx = None
        self.job = 0

    def fault(self, target, op, *actions):
        self.faults.setdefault((target, op), []).extend(actions)

    def next_fault(self, target, op):
        queue = self.faults.get((target, op))

        return queue.pop(0) if queue else None

    def ops(self, target, op):
        return [call for call in self.calls if call[0] == target and call[1] == op]

    def devices(self):
        if self.timeline:
            self.present = set(self.timeline.pop(0))

        return set(self.present)

    def external_disks(self):
        if self.disk_timeline:
            self.disks = set(self.disk_timeline.pop(0))

        return set(self.disks)

    def reboot(self):
        if self.on_reboot:
            self.timeline = [set(step) for step in self.on_reboot]

    def listdir(self, path):
        return self.dirs.get(path, [])

    def worker_op(self, op, data):
        if op == 6:
            return 0, struct.pack('<4I', self.worker_version, 0xfffffffe, 0, self.commands)

        if op == 9:
            return 0, struct.pack('<6I', self.worker_version, 0x3f, 16 << 20, 1000, 0, self.worker_features)

        if op == 16:
            index, = struct.unpack_from('<I', data)
            names = self.listdir(data[4:].decode())

            return 0, (b'\0' + names[index].encode()) if index < len(names) else b''

        if op == 17:
            offset, = struct.unpack_from('<Q', data)
            path = data[8:].decode()

            return (0, self.files[path][offset:offset + 4080]) if path in self.files else (2, b'')

        if op == 18:
            content = self.files.get(data.decode())

            return (2, b'') if content is None else (0, struct.pack('<Q', len(content)) +
                                                     hashlib.sha256(content).digest())

        if op == 32:
            self.tx = {'path': data[73:].decode(), 'data': bytearray()}
        elif op == 33:
            self.tx['data'].extend(data[8:])
        elif op == 34:
            self.files[self.tx['path']] = bytes(self.tx['data'])
            self.tx = None
        elif op == 40:
            self.pad_ready = 1

            return 0, struct.pack('<II', 2 if data == b'\2' else 0, 0)
        elif op == 41:
            self.pad_ready = 1
        elif op == 42:
            self.pad_ready = 0
        elif op == 43:
            if self.screenshot_error:
                return self.screenshot_error, b''

            self.jpeg = b'\xff\xd8' + b'j' * 100 + b'\xff\xd9'

            return 0, struct.pack('<Q', len(self.jpeg))
        elif op == 44:
            offset, = struct.unpack_from('<Q', data)

            return 0, self.jpeg[offset:offset + 4080]
        elif op == 45:
            return 0, struct.pack('<IQQ', 0, 0x77, 0x0100000000001000)
        elif op in (46, 53):
            self.reboot()
        elif op == 47:
            return self.rename_flag(data[0], data[1:17].decode()), b''
        elif op == 48:
            return 0, struct.pack('<4I', self.pad_ready, 0, 0, 0)
        elif op == 49:
            return 0, struct.pack('<IiiI', 0, 0, 0, int(bool(self.lease)))
        elif op == 50:
            self.lease, = struct.unpack('<I', data)
        elif op == 52:
            self.seq_active = 0
        elif op == 54:
            return 0, struct.pack('<12I', 1, 0, self.pad_ready, self.seq_active, 0, 0, 0, 600000,
                                  int(bool(self.lease)), self.wake_alarm, 0xffffffff, 0)
        elif op == 55:
            self.wake_alarm, = struct.unpack('<I', data)
        else:
            return 22, b''

        return 0, b''

    def rename_flag(self, enable, tid):
        names = self.dirs.setdefault(f'sdmc:/atmosphere/contents/{tid}/flags', [])
        source, target = ('boot2.flag.sidecar-disabled', 'boot2.flag') if enable else \
            ('boot2.flag', 'boot2.flag.sidecar-disabled')

        if source not in names:
            return 2

        if target in names:
            return 17

        names[names.index(source)] = target

        return 0

    def rescue_op(self, op, data):
        if op == 6:
            return 0, struct.pack('<4I', self.rescue_version, 0x52534355, 1, 0)

        if op == 9:
            return 0, struct.pack('<6I', self.rescue_version, self.launch_ready, 16 << 20, 0, 0, self.rescue_features)

        if op == 60:
            return 0, struct.pack('<IQ', 0 if self.worker_running else 0x20f, 0x55 if self.worker_running else 0)

        if op in (62, 64):
            self.starts += 1
            self.worker_running = self.worker_running or self.start_brings_up
            self.job += 1

            return 0, struct.pack('<Q', 0x55 if op == 62 else self.job)

        if op == 65:
            return 0, struct.pack('<IIQQ', 3, 0, 0x55, self.job)

        if op == 66:
            return 0, b''

        if op == 70:
            if self.guard:
                return 17, b''

            count, = struct.unpack_from('<I', data)
            self.guard = {'armed': 1, 'boots': 0, 'entries': count, 'raw': data}

            return 0, b''

        if op == 71:
            if not self.guard:
                return 2, b''

            self.guard = None

            return 0, b''

        if op == 72:
            g = self.guard or {'armed': 0, 'boots': 0, 'entries': 0}

            return 0, struct.pack('<6I', g['armed'], g['boots'], 2, g['entries'], 0, 0)

        return 22, b''


class FakeUsb:
    """Mimics UsbSocket: a timeout faults the stream until drained on a new socket."""

    def __init__(self, console, target):
        self.console, self.target = console, target
        self.faulted = self.closed = False

    def command(self, op, data=b''):
        if self.closed or self.faulted:
            raise RuntimeError('USB stream is closed or reply state is uncertain; no command sent')

        console = self.console
        console.attempts.append((self.target, op))
        action = console.next_fault(self.target, op)

        if action == 'timeout_before':
            self.faulted = True

            raise usb_timeout()

        if self.target == 'worker' and not console.worker_running:
            return 111, b'', 0

        if isinstance(action, int):
            return action, b'', 0

        console.calls.append((self.target, op, bytes(data)))
        handler = console.worker_op if self.target == 'worker' else console.rescue_op
        error, payload = handler(op, bytes(data))

        if self.target == 'worker':
            console.commands += 1

        if action == 'timeout':
            self.faulted = True

            raise usb_timeout()

        return error, payload, 0

    def close(self):
        self.closed = True


class FakeNyx:
    def __init__(self, console):
        self.console = console
        self.faulted = False

    def call(self, op, *args):
        if self.faulted:
            raise RuntimeError('Previous reply uncertain; no operation replay allowed')

        console = self.console
        action = console.next_fault('nyx', op)

        if action == 'timeout_before':
            self.faulted = True

            raise usb_timeout()

        console.calls.append(('nyx', op, args))

        if op > 5 and console.nyx_version < 2:
            raise RuntimeError('Nyx error 1')

        reply = b''

        if op == 1:
            reply = struct.pack('<6I', console.nyx_version, 1280, 720, 4, FB_SIZE, 500)
        elif op == 2:
            reply = struct.pack('<I', 5)
        elif op == 3:
            reply = bytes(args[2])
        elif op == 6:
            reply = b'1\t4mosphere\n2\tRemote USB Control\n'
        elif op in (7, 8, 9, 10):
            console.reboot()
        elif op == 11:
            console.watchdog = args[0]
        elif op == 12:
            reply = struct.pack('<4I', 2, getattr(console, 'watchdog', 90), int(bool(getattr(console, 'watchdog', 90))), 1)

        if action == 'timeout':
            self.faulted = True

            raise usb_timeout()

        return reply

    def close(self):
        pass


class FakeMailbox:
    def __init__(self, console):
        self.console = console
        self.status = {'version': 1, 'token': '00000000000000ab', 'now_ms': 1000, 'sequence': 5,
                       'phase': 'done', 'result': 0, 'reply': ''}
        self.submits = []
        self.ignore = False

    def read_status(self):
        if MTP not in self.console.present:
            raise OpError('absent', 'Mailbox status is not readable over MTP')

        return dict(self.status)

    def submit(self, request):
        self.submits.append(request)

        if self.ignore:
            return 0

        _, token, sequence, _, op, length = struct.unpack_from('<8sQQQII', request)

        if token != int(self.status['token'], 16) or sequence <= self.status['sequence']:
            return 0

        data = request[40:40 + length]
        self.console.calls.append(('mailbox', op, data))
        error, payload = self.console.worker_op(op, data)
        self.status.update(sequence=sequence, phase='done', result=error, reply=payload[:64].hex())

        return 0


class FakeLinks:
    def __init__(self, console):
        self.console = console
        self.mailbox = FakeMailbox(console)

    def devices(self):
        return self.console.devices()

    def open(self, target):
        console = self.console
        console.opens[target] += 1

        if target in console.locked:
            raise RuntimeError('Another sidecar/debugger client owns USB')

        if target == 'nyx':
            if NYX not in console.present:
                raise RuntimeError('Remote Nyx is not connected')

            return FakeNyx(console)

        if HOS not in console.present:
            raise RuntimeError('USB Debug Relay is not running or cable is disconnected')

        return FakeUsb(console, target)

    def drain(self, target):
        self.console.drains[target] += 1

        return 1

    def external_disks(self):
        return self.console.external_disks()


def make_daemon(console=None, config=None):
    console = console or FakeConsole()
    tmp = Path(tempfile.mkdtemp(prefix='switchd-test-'))
    daemon = Daemon(FakeLinks(console), clock=FakeClock(), events=EventLog(tmp / 'events.jsonl'),
                    state_dir=tmp / 'state', config=config or {})
    daemon.monitor.poll()

    return daemon, console, tmp
