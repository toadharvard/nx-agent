"""Host-side file locations. Every path can be overridden through NXAGENT_* variables."""
import os
import sys
from pathlib import Path

APP = 'nx-agent'
SOCKET_LIMIT = 100


def _env_path(name):
    value = os.environ.get(name)

    return Path(value).expanduser() if value else None


def state_dir():
    override = _env_path('NXAGENT_STATE_DIR')

    if override:
        return override

    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / APP

    base = _env_path('XDG_STATE_HOME') or Path.home() / '.local' / 'state'

    return base / APP


def config_dir():
    override = _env_path('NXAGENT_CONFIG_DIR')

    if override:
        return override

    if sys.platform == 'darwin':
        return state_dir()

    base = _env_path('XDG_CONFIG_HOME') or Path.home() / '.config'

    return base / APP


def runtime_dir():
    override = _env_path('NXAGENT_RUNTIME_DIR')

    if override:
        return override

    xdg = _env_path('XDG_RUNTIME_DIR')

    if xdg and sys.platform != 'darwin':
        return xdg / APP

    return Path('/tmp') / f'{APP}-{os.getuid()}'


def ensure(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)

    return path


def socket_path():
    override = _env_path('NXAGENT_SOCKET')
    path = override or runtime_dir() / 'nxagentd.sock'

    if len(str(path)) >= SOCKET_LIMIT:
        path = Path('/tmp') / f'{APP}-{os.getuid()}' / 'nxagentd.sock'

    return path


def pid_path():
    return runtime_dir() / 'nxagentd.pid'


def lock_path():
    return runtime_dir() / 'nxagentd.lock'


def usb_lock(name):
    return ensure(runtime_dir()) / name


def daemon_state_dir():
    return state_dir() / 'daemon'


def journal_dir():
    return state_dir() / 'journal'


def operations_dir():
    return state_dir() / 'operations'


def events_path():
    return _env_path('NXAGENT_EVENTS') or state_dir() / 'events.jsonl'


def daemon_log_path():
    return state_dir() / 'nxagentd.log'


def config_path():
    return _env_path('NXAGENT_CONFIG') or config_dir() / 'config.json'


def tool_path(name):
    override = _env_path('NXAGENT_MTP_BIN') if name.startswith('mtp_') else None

    if override and (override / name).exists():
        return override / name

    for directory in os.environ.get('PATH', '').split(os.pathsep):
        candidate = Path(directory) / name

        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    return state_dir() / 'bin' / name
