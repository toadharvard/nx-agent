#!/usr/bin/env python3
"""nxagentd: the only owner of the Switch USB interfaces. JSON-lines API on a Unix socket."""
import argparse
import fcntl
import json
import os
import signal
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import paths
from .common import (EVENTS_PATH, LOCK_PATH, PID_PATH, SOCKET_PATH, OpError,
                    classify)
from .ops import Daemon

COMMANDS = {
    'status': lambda d, live=True: d.status(live),
    'session.start': lambda d, minutes, mode='rail', wake_alarm=None: d.session.start(minutes, mode, wake_alarm),
    'session.stop': lambda d: d.session.stop('requested'),
    'session.status': lambda d: d.session.status(),
    'screen': lambda d, path, layer=None: d.screen(path, layer),
    'press': lambda d, buttons, ms=120, sticks=(0, 0, 0, 0): d.press(buttons, ms, sticks),
    'hold': lambda d, buttons, ms: d.sequence([{'buttons': buttons, 'ms': ms}]),
    'seq': lambda d, steps, wait=True: d.sequence(steps, wait),
    'attach': lambda d, mode='rail': d.attach(mode),
    'detach': lambda d: d.detach(),
    'get': lambda d, remote, local: d.get(remote, local),
    'put': lambda d, remote, local, create=False: d.put(remote, local, create),
    'ls': lambda d, remote: d.ls(remote),
    'app': lambda d: d.app(),
    'reboot': lambda d, force=False: d.reboot(force),
    'reboot_to': lambda d, target, entry=None, wait=True, timeout=120, force=False: d.reboot_to(target, entry, wait,
                                                                                                 timeout, force),
    'bootflag': lambda d, tid, enable, unguarded=False: d.bootflag(tid, enable, unguarded),
    'guard.arm': lambda d, disable=(), enable=(), apply=False, threshold=2: d.guard.arm(disable, enable, apply,
                                                                                        threshold),
    'guard.confirm': lambda d, force=False: d.guard.confirm(force),
    'guard.status': lambda d: d.guard.status(),
    'sleep_settings': lambda d: d.sleep_settings(),
    'autohold': lambda d, enable: d.autohold(enable),
    'logs.collect': lambda d, local: d.collect(local),
    'mtp.status': lambda d: d.mtp_status(),
    'hekate.screen': lambda d, path: d.nyx_screen(path),
    'hekate.tap': lambda d, x, y, ms=150: d.nyx_tap(x, y, ms),
    'hekate.drag': lambda d, x1, y1, x2, y2: d.nyx_drag(x1, y1, x2, y2),
    'hekate.release': lambda d: d.nyx_release(),
    'hekate.entries': lambda d: d.nyx_entries(),
    'hekate.status': lambda d: d.nyx_status(),
    'hekate.watchdog': lambda d, seconds: d.nyx_watchdog(seconds),
    'hekate.action': lambda d, action, wait=True, timeout=180: d.nyx_action(action, wait, timeout),
    'wait_state': lambda d, state, timeout=60: d.wait_state(state, timeout),
    'ums.put': lambda d, files, hooks=(), wait=False: d.ums_put(files, hooks, wait),
    'job.status': lambda d, job_id=None: d.jobs.status(job_id),
    'console.update': lambda d, action, sha256=None, image=None, fallback_sha256=None, device_tested=False:
        d.console_update(action, sha256, image, fallback_sha256, device_tested),
}


def dispatch(daemon, request):
    command = request.get('cmd')
    handler = COMMANDS.get(command)

    if handler is None:
        return {'ok': False, 'kind': 'invalid', 'error': f'unknown command {command!r}'}

    try:
        response = {'ok': True, 'result': handler(daemon, **(request.get('args') or {}))}
    except OpError as error:
        response = {'ok': False, **error.to_dict()}
    except TypeError as error:
        response = {'ok': False, 'kind': 'invalid', 'error': str(error)}
    except Exception as error:
        response = {'ok': False, **classify(error, None).to_dict()}

    daemon.events.emit('request', cmd=command, ok=response['ok'], kind=response.get('kind'))

    return response


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(1 << 20)

        try:
            request = json.loads(line)
        except ValueError:
            response = {'ok': False, 'kind': 'invalid', 'error': 'request must be one JSON line'}
        else:
            if request.get('cmd') == 'shutdown':
                response = {'ok': True, 'result': {'stopping': True}}
                self.server.stop_event.set()
            else:
                response = dispatch(self.server.owner, request)

        self.wfile.write((json.dumps(response, default=str) + '\n').encode())


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class AlreadyRunning(RuntimeError):
    pass


def acquire_singleton(lock_path=LOCK_PATH, pid_path=PID_PATH):
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open('a')

    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        pid = Path(pid_path).read_text().strip() if Path(pid_path).exists() else '?'

        raise AlreadyRunning(f'nxagentd is already running (pid {pid}); refusing a second USB owner')

    Path(pid_path).write_text(f'{os.getpid()}\n')

    return handle


def make_server(daemon, socket_path=SOCKET_PATH):
    socket_path = Path(socket_path)
    socket_path.unlink(missing_ok=True)
    server = Server(str(socket_path), Handler)
    os.chmod(socket_path, 0o600)
    server.owner = daemon
    server.stop_event = threading.Event()

    return server


def loop(stop, interval, step, events, name):
    while not stop.is_set():
        try:
            step()
        except Exception as error:
            events.emit('loop_error', loop=name, error=repr(error))

        stop.wait(interval)


def scheduler_step(daemon):
    daemon.guard.tick()
    daemon.session.tick()


def serve(links=None, socket_path=SOCKET_PATH, lock_path=LOCK_PATH, pid_path=PID_PATH, **daemon_kwargs):
    lock = acquire_singleton(lock_path, pid_path)

    if links is None:
        from .links import UsbLinks

        links = UsbLinks()

    daemon = Daemon(links, **daemon_kwargs)
    server = make_server(daemon, socket_path)
    stop = server.stop_event
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threads = [threading.Thread(target=server.serve_forever, name='nxagentd-api', daemon=True),
               threading.Thread(target=loop, args=(stop, 0.5, daemon.monitor.poll, daemon.events, 'monitor'),
                                name='nxagentd-monitor', daemon=True),
               threading.Thread(target=loop, args=(stop, 1.0, lambda: scheduler_step(daemon), daemon.events,
                                                   'scheduler'), name='nxagentd-scheduler', daemon=True)]

    for thread in threads:
        thread.start()

    daemon.events.emit('daemon_start', pid=os.getpid(), socket=str(socket_path))
    print(f'nxagentd pid {os.getpid()} listening on {socket_path}', flush=True)

    try:
        stop.wait()
    finally:
        daemon.session.stop('daemon_shutdown')
        server.shutdown()
        server.server_close()
        daemon.shutdown()
        Path(socket_path).unlink(missing_ok=True)
        Path(pid_path).unlink(missing_ok=True)
        daemon.events.emit('daemon_stop', pid=os.getpid())
        lock.close()


def socket_ready(path):
    import socket

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    try:
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()

    return True


def start_background():
    if socket_ready(SOCKET_PATH):
        print(f'nxagentd is already running ({SOCKET_PATH})')

        return 0

    log = paths.daemon_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    with log.open('a') as stream:
        process = subprocess.Popen([sys.executable, '-m', 'nxagent.daemon', '--foreground'],
                                   stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True, cwd=paths.ensure(paths.state_dir()))

    deadline = time.monotonic() + 10

    while time.monotonic() < deadline:
        if socket_ready(SOCKET_PATH):
            print(f'nxagentd started in background (pid {process.pid}); log {log}; events {EVENTS_PATH}')

            return 0

        if process.poll() is not None:
            print(f'nxagentd exited with {process.returncode}; see {log}', file=sys.stderr)

            return 1

        time.sleep(0.2)

    print(f'nxagentd did not open {SOCKET_PATH} within 10s; see {log}', file=sys.stderr)

    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--foreground', action='store_true')
    mode.add_argument('--background', action='store_true')
    args = parser.parse_args(argv)

    if args.background:
        return start_background()

    try:
        serve()
    except AlreadyRunning as error:
        print(error, file=sys.stderr)

        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
