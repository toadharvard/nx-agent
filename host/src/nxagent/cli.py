#!/usr/bin/env python3
"""nxctl: CLI client of nxagentd. Exit codes: 0 ok, 1 daemon not running / internal,
2 absent, 3 worker_down, 4 uncertain, 5 device_error, 6 busy, 64 usage."""
import argparse
import json
import sys
import time
from pathlib import Path

from .client import DaemonDown, request
from .common import EVENTS_PATH, EXIT_CODES, SOCKET_PATH

START_HINT = """nxagentd is not running. Start it with:
  nxagentd --background      (or: nxagentd --foreground)"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f'{self.prog}: error: {message}', file=sys.stderr)
        sys.exit(EXIT_CODES['invalid'])


def absolute(path):
    return str(Path(path).expanduser().resolve())


def build_parser():
    parser = Parser(prog='nxctl', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('status')
    p.add_argument('--cached', action='store_true', help='do not touch USB')

    p = sub.add_parser('events')
    p.add_argument('-n', type=int, default=20)
    p.add_argument('-f', '--follow', action='store_true')

    p = sub.add_parser('session')
    s = p.add_subparsers(dest='action', required=True)
    start = s.add_parser('start')
    start.add_argument('--minutes', type=int, required=True)
    start.add_argument('--mode', choices=['rail', 'fullkey'], default='rail')
    start.add_argument('--wake-alarm', type=int, default=None, help='seconds (60..3600), 0 disables; default 120')
    s.add_parser('stop')
    s.add_parser('status')

    p = sub.add_parser('screen')
    p.add_argument('path')
    p.add_argument('--layer', type=int)

    p = sub.add_parser('press')
    p.add_argument('buttons', help='e.g. A, HOME, ZL+ZR')
    p.add_argument('--ms', type=int, default=120)

    for stick in ('lx', 'ly', 'rx', 'ry'):
        p.add_argument(f'--{stick}', type=int, default=0)

    p = sub.add_parser('hold')
    p.add_argument('buttons')
    p.add_argument('--ms', type=int, required=True)

    p = sub.add_parser('seq')
    p.add_argument('file', help='JSON list of {buttons, ms, lx, ly, rx, ry}')
    p.add_argument('--no-wait', action='store_true')

    p = sub.add_parser('attach')
    p.add_argument('--mode', choices=['rail', 'fullkey'], default='rail')
    sub.add_parser('detach')

    p = sub.add_parser('get')
    p.add_argument('remote')
    p.add_argument('local')

    p = sub.add_parser('put')
    p.add_argument('remote')
    p.add_argument('local')
    p.add_argument('--create', action='store_true')

    p = sub.add_parser('ls')
    p.add_argument('remote')

    sub.add_parser('app')
    sub.add_parser('reboot').add_argument('--force', action='store_true', help='skip the post-boot uptime guard')

    p = sub.add_parser('reboot-to')
    p.add_argument('target', choices=['ums', 'remote', 'menu', 'entry'])
    p.add_argument('index', nargs='?', type=int)
    p.add_argument('--no-wait', action='store_true')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--force', action='store_true', help='skip the post-boot uptime guard')

    p = sub.add_parser('wait')
    p.add_argument('state', choices=['hos', 'hekate_remote', 'mtp', 'ums', 'absent'])
    p.add_argument('--timeout', type=int, default=60)

    p = sub.add_parser('ums-put', help='copy files to the SD card through Remote Hekate UMS, then boot HOS')
    p.add_argument('pairs', nargs='+', metavar='REMOTE=LOCAL', help='e.g. switch/.packages/x/config.ini=./config.ini')
    p.add_argument('--hook', action='append', default=[], choices=['clear_4ifir_alarm'])
    p.add_argument('--wait', action='store_true', help='block until done instead of returning a job id')

    p = sub.add_parser('job')
    p.add_argument('job_id', nargs='?')

    p = sub.add_parser('console', help='Worker sysmodule maintenance through Rescue')
    p.add_argument('action', choices=['status', 'install', 'promote', 'rollback'])
    p.add_argument('--sha256')
    p.add_argument('--image')
    p.add_argument('--fallback-sha256')
    p.add_argument('--device-tested', action='store_true')

    p = sub.add_parser('install', help='install an nx-agent SD bundle onto a mounted SD card (no daemon needed)')
    p.add_argument('--sd', required=True, help='SD card mount point')
    p.add_argument('--bundle', required=True, help='release zip or extracted directory')
    p.add_argument('--apply', action='store_true', help='write changes (default is a dry run)')
    p.add_argument('--force', action='store_true', help='overwrite unknown sysmodules with the same title IDs')

    p = sub.add_parser('skill', help='install the agent skill for Claude Code')
    p.add_argument('action', choices=['install'])
    p.add_argument('--dest', default=str(Path.home() / '.claude' / 'skills'))

    p = sub.add_parser('setup', help='build optional native helpers')
    p.add_argument('what', choices=['mtp'])

    p = sub.add_parser('guard')
    g = p.add_subparsers(dest='action', required=True)
    arm = g.add_parser('arm')
    arm.add_argument('--disable-flag', action='append', default=[], metavar='TID')
    arm.add_argument('--enable-flag', action='append', default=[], metavar='TID')
    arm.add_argument('--apply', action='store_true', help='perform the flag renames after arming')
    arm.add_argument('--threshold', type=int, default=2, help='host fallback only: unconfirmed boots')
    confirm = g.add_parser('confirm')
    confirm.add_argument('--force', action='store_true', help='skip the healthy-boot check')
    g.add_parser('status')

    p = sub.add_parser('bootflag')
    p.add_argument('tid')
    p.add_argument('state', choices=['enable', 'disable'])
    p.add_argument('--unguarded', action='store_true')

    p = sub.add_parser('hekate')
    h = p.add_subparsers(dest='action', required=True)
    h.add_parser('screen').add_argument('path')
    tap = h.add_parser('tap')
    tap.add_argument('x', type=int)
    tap.add_argument('y', type=int)
    tap.add_argument('--ms', type=int, default=150)
    drag = h.add_parser('drag')

    for name in ('x1', 'y1', 'x2', 'y2'):
        drag.add_argument(name, type=int)

    for name in ('release', 'entries', 'status', 'reboot', 'poweroff'):
        h.add_parser(name)

    for name in ('launch', 'ums'):
        action = h.add_parser(name)
        action.add_argument('--no-wait', action='store_true')
        action.add_argument('--timeout', type=int, default=180)

    h.add_parser('watchdog').add_argument('seconds', type=int)

    sub.add_parser('sleep-settings')
    p = sub.add_parser('logs')
    p.add_argument('action', choices=['collect'])
    p.add_argument('dir')
    sub.add_parser('mtp-status')
    sub.add_parser('daemon-stop')

    return parser


def to_request(a):
    simple = {'app': 'app', 'detach': 'detach', 'sleep-settings': 'sleep_settings',
              'mtp-status': 'mtp.status', 'daemon-stop': 'shutdown'}

    if a.cmd in simple:
        return simple[a.cmd], {}

    if a.cmd == 'status':
        return 'status', {'live': not a.cached}

    if a.cmd == 'session':
        args = {'minutes': a.minutes, 'mode': a.mode, 'wake_alarm': a.wake_alarm} if a.action == 'start' else {}

        return f'session.{a.action}', args

    if a.cmd == 'screen':
        return 'screen', {'path': absolute(a.path), 'layer': a.layer}

    if a.cmd == 'press':
        return 'press', {'buttons': a.buttons, 'ms': a.ms, 'sticks': [a.lx, a.ly, a.rx, a.ry]}

    if a.cmd == 'hold':
        return 'hold', {'buttons': a.buttons, 'ms': a.ms}

    if a.cmd == 'seq':
        steps = json.loads(Path(a.file).read_text())

        return 'seq', {'steps': steps['steps'] if isinstance(steps, dict) else steps, 'wait': not a.no_wait}

    if a.cmd == 'attach':
        return 'attach', {'mode': a.mode}

    if a.cmd in ('get', 'put'):
        args = {'remote': a.remote, 'local': absolute(a.local)}

        if a.cmd == 'put':
            args['create'] = a.create

        return a.cmd, args

    if a.cmd == 'ls':
        return 'ls', {'remote': a.remote}

    if a.cmd == 'reboot-to':
        return 'reboot_to', {'target': a.target, 'entry': a.index, 'wait': not a.no_wait, 'timeout': a.timeout,
                             'force': a.force}

    if a.cmd == 'reboot':
        return 'reboot', {'force': a.force}

    if a.cmd == 'wait':
        return 'wait_state', {'state': a.state, 'timeout': a.timeout}

    if a.cmd == 'ums-put':
        files = []

        for pair in a.pairs:
            remote, _, local = pair.partition('=')

            if not remote or not local:
                raise SystemExit(f'nxctl: bad pair {pair!r}, expected REMOTE=LOCAL')

            files.append({'remote': remote, 'local': absolute(local)})

        return 'ums.put', {'files': files, 'hooks': a.hook, 'wait': a.wait}

    if a.cmd == 'job':
        return 'job.status', {'job_id': a.job_id}

    if a.cmd == 'console':
        return 'console.update', {'action': a.action, 'sha256': a.sha256,
                                  'image': absolute(a.image) if a.image else None,
                                  'fallback_sha256': a.fallback_sha256, 'device_tested': a.device_tested}

    if a.cmd == 'guard':
        if a.action == 'arm':
            return 'guard.arm', {'disable': a.disable_flag, 'enable': a.enable_flag, 'apply': a.apply,
                                 'threshold': a.threshold}

        return f'guard.{a.action}', {'force': a.force} if a.action == 'confirm' else {}

    if a.cmd == 'bootflag':
        return 'bootflag', {'tid': a.tid, 'enable': a.state == 'enable', 'unguarded': a.unguarded}

    if a.cmd == 'logs':
        return 'logs.collect', {'local': absolute(a.dir)}

    return hekate_request(a)


def hekate_request(a):
    if a.action == 'screen':
        return 'hekate.screen', {'path': absolute(a.path)}

    if a.action == 'tap':
        return 'hekate.tap', {'x': a.x, 'y': a.y, 'ms': a.ms}

    if a.action == 'drag':
        return 'hekate.drag', {'x1': a.x1, 'y1': a.y1, 'x2': a.x2, 'y2': a.y2}

    if a.action == 'watchdog':
        return 'hekate.watchdog', {'seconds': a.seconds}

    if a.action in ('release', 'entries', 'status'):
        return f'hekate.{a.action}', {}

    args = {'action': a.action}

    if a.action in ('launch', 'ums'):
        args.update(wait=not a.no_wait, timeout=a.timeout)

    return 'hekate.action', args


def events(n, follow, path=EVENTS_PATH):
    path = Path(path)

    if not path.exists():
        print(f'no events yet ({path})', file=sys.stderr)

        return 0

    lines = path.read_text().splitlines()

    for line in lines[-n:] if n > 0 else []:
        print(line)

    if not follow:
        return 0

    with path.open() as stream:
        stream.seek(0, 2)

        try:
            while True:
                line = stream.readline()

                if line:
                    print(line, end='', flush=True)
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return 0


def local_install(args):
    from .common import OpError
    from .workflows.sd_install import install

    try:
        print(json.dumps(install(args.sd, args.bundle, args.apply, args.force), indent=2))
    except OpError as error:
        print(f'nxctl: {error.kind}: {error}', file=sys.stderr)

        return EXIT_CODES.get(error.kind, 1)

    return 0


def local_skill(args):
    import shutil

    source = Path(__file__).resolve().parent / 'data' / 'skill'

    if not source.exists():
        print(f'nxctl: skill files not packaged ({source})', file=sys.stderr)

        return 1

    dest = Path(args.dest).expanduser() / 'nx-agent'
    shutil.copytree(source, dest, dirs_exist_ok=True)
    print(f'skill installed to {dest}')

    return 0


def local_setup(args):
    import subprocess

    from . import paths

    native = Path(__file__).resolve().parent / 'data' / 'native'
    out = paths.ensure(paths.state_dir() / 'bin')
    result = subprocess.run(['make', '-C', str(native), f'OUT={out}'])

    if result.returncode == 0:
        print(f'MTP helpers built into {out}')

    return result.returncode


LOCAL = {'install': local_install, 'skill': local_skill, 'setup': local_setup}


def main(argv=None, socket_path=SOCKET_PATH):
    args = build_parser().parse_args(argv)

    if args.cmd == 'events':
        return events(args.n, args.follow)

    if args.cmd in LOCAL:
        return LOCAL[args.cmd](args)

    cmd, payload = to_request(args)

    try:
        response = request(cmd, payload, socket_path)
    except DaemonDown:
        print(START_HINT, file=sys.stderr)

        return 1

    print(json.dumps(response.get('result') if response.get('ok') else response, indent=2, default=str))

    if response.get('ok'):
        return 0

    print(f"nxctl: {response.get('kind')}: {response.get('error')}", file=sys.stderr)

    return EXIT_CODES.get(response.get('kind'), 1)


if __name__ == '__main__':
    sys.exit(main())
