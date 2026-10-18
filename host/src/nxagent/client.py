"""JSON-lines client of nxagentd shared by nxctl and the MCP server."""
import json
import socket
import subprocess
import sys
import time

from .common import SOCKET_PATH


class DaemonDown(Exception):
    pass


def request(cmd, args=None, socket_path=SOCKET_PATH, timeout=900):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)

    try:
        client.connect(str(socket_path))
    except OSError as error:
        client.close()

        raise DaemonDown(str(error))

    try:
        client.sendall((json.dumps({'cmd': cmd, 'args': args or {}}) + '\n').encode())
        data = b''

        while not data.endswith(b'\n'):
            chunk = client.recv(65536)

            if not chunk:
                break

            data += chunk
    finally:
        client.close()

    return json.loads(data)


def ensure_daemon(socket_path=SOCKET_PATH, seconds=15):
    try:
        return request('status', {'live': False}, socket_path, timeout=5)
    except DaemonDown:
        pass

    subprocess.run([sys.executable, '-m', 'nxagent.daemon', '--background'], capture_output=True, timeout=seconds)
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        try:
            return request('status', {'live': False}, socket_path, timeout=5)
        except DaemonDown:
            time.sleep(0.3)

    raise DaemonDown(f'nxagentd did not start; see the log in the state directory ({socket_path})')
