"""Shared constants, error model and event log for nxagentd / nxctl."""
import collections
import json
import re
import threading
import time
from pathlib import Path

from . import paths

STATE_DIR = paths.daemon_state_dir()
SOCKET_PATH = paths.socket_path()
PID_PATH = paths.pid_path()
LOCK_PATH = paths.lock_path()
EVENTS_PATH = paths.events_path()
CONFIG_PATH = paths.config_path()

HOS, NYX, MTP = '057e:3000', '11ec:a7e1', '057e:201d'
KNOWN_DEVICES = (HOS, NYX, MTP)

KINDS = ('absent', 'worker_down', 'uncertain', 'device_error', 'busy', 'invalid')
EXIT_CODES = {'ok': 0, 'absent': 2, 'worker_down': 3, 'uncertain': 4,
              'device_error': 5, 'busy': 6, 'invalid': 64}

READ_ONLY = {
    'worker': frozenset({6, 9, 16, 17, 18, 43, 44, 45, 48, 49, 54}),
    'rescue': frozenset({6, 9, 17, 18, 60, 65, 72, 81}),
    'nyx': frozenset({1, 2, 3, 6, 12}),
}
MAX_READ_RETRIES = 3

BRIDGE_ERRNO = {111: 'ECONNREFUSED', 104: 'ECONNRESET', 110: 'ETIMEDOUT', 116: 'ETIMEDOUT(newlib)'}
ERRNO_NAMES = {1: 'EPERM', 2: 'ENOENT', 5: 'EIO', 13: 'EACCES', 16: 'EBUSY', 17: 'EEXIST',
               22: 'EINVAL', 28: 'ENOSPC', 71: 'EPROTO', 139: 'EOVERFLOW'}

WORKER_FEATURES = {1: 'rail_pair', 2: 'sequences', 4: 'r2c', 8: 'wake_alarm',
                   16: 'wake_on_power_change', 32: 'extended_state', 256: 'file_tree'}


def is_read_only(target, ops):
    return all(op in READ_ONLY[target] for op in ops)


def feature_names(mask):
    return [name for bit, name in WORKER_FEATURES.items() if mask & bit]


class OpError(Exception):
    def __init__(self, kind, message, **details):
        if kind not in KINDS:
            raise ValueError(f'unknown error kind {kind}')

        super().__init__(message)
        self.kind = kind
        self.details = details

    def to_dict(self):
        return {'kind': self.kind, 'error': str(self), **self.details}


def console_error(code, target, op):
    if target == 'worker' and code in BRIDGE_ERRNO:
        return OpError('worker_down', f'Worker unreachable through Rescue bridge: {BRIDGE_ERRNO[code]}',
                       code=code, op=op, executed=False if code == 111 else 'unknown')

    name = ERRNO_NAMES.get(code, f'0x{code:x}')

    return OpError('device_error', f'{target} op{op}: console error {name}', code=code, op=op)


_CONSOLE = re.compile(r'command (\d+): console error 0x([0-9a-fA-F]+)')
_NYX = re.compile(r'Nyx error (\d+)')
_ABSENT = ('is not running or cable is disconnected', 'Remote Nyx is not connected')
_UNCERTAIN = ('reply state is uncertain', 'Previous reply uncertain', 'Reply mismatch')


def classify(exc, target):
    if isinstance(exc, OpError):
        return exc

    message = str(exc)

    if isinstance(exc, BlockingIOError) or 'owns USB' in message:
        return OpError('busy', 'Another client holds the USB lock for this interface')

    if any(text in message for text in _ABSENT):
        return OpError('absent', message)

    match = _CONSOLE.search(message)

    if match:
        return console_error(int(match.group(2), 16), target, int(match.group(1)))

    match = _NYX.search(message)

    if match:
        return OpError('device_error', f'Remote Hekate error {match.group(1)}', code=int(match.group(1)))

    if isinstance(exc, (OSError, TimeoutError)) or any(text in message for text in _UNCERTAIN):
        return OpError('uncertain', message or type(exc).__name__)

    if isinstance(exc, ValueError):
        return OpError('invalid', message)

    return OpError('device_error', message or type(exc).__name__)


class EventLog:
    def __init__(self, path=EVENTS_PATH, keep=200):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.recent = collections.deque(maxlen=keep)

    def emit(self, event, **fields):
        row = {'time_ns': time.time_ns(), 'event': event, **fields}
        line = json.dumps(row, default=str)

        with self.lock:
            self.recent.append(row)
            self.path.parent.mkdir(parents=True, exist_ok=True)

            with self.path.open('a') as stream:
                stream.write(line + '\n')

        return row


def load_config(path=CONFIG_PATH):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
