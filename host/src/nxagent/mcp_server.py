"""nxagent-mcp: MCP server (stdio) that lets an agent control a CFW Switch through nxagentd.

Profiles gate which tools exist at all: readonly < interactive (default) < full.
The console-side write policy and the daemon guards stay the hard safety boundary.
"""
import argparse
import base64
import io
import json
import os
import sys
import tempfile
from importlib import resources
from pathlib import Path

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import DaemonDown, ensure_daemon, request
from .common import EVENTS_PATH

PROFILES = ('readonly', 'interactive', 'full')
HINTS = {
    'absent': 'The console is not on USB in the needed mode. Check nx_status; the cable, sleep or a USB-C role flip '
              '(console powering the host) can cause this. Ask the human to replug if it persists.',
    'worker_down': 'The Worker sysmodule is not answering; the daemon tries to restart it through Rescue. '
                   'Wait a few seconds and call nx_status.',
    'uncertain': 'The reply was lost. Do NOT retry the mutation. Verify the outcome with read-only tools first.',
    'busy': 'Another client or job holds USB. Poll nx_job_status or retry later.',
    'device_error': 'The console rejected the request (see message). Writes outside the allow-list return EACCES; '
                    'use nx_sd_put_offline for those paths.',
    'invalid': 'Fix the arguments; nothing was sent.',
}


def data_text(name):
    return resources.files('nxagent').joinpath('data', name).read_text()


def call(cmd, **args):
    try:
        ensure_daemon()
        response = request(cmd, args)
    except DaemonDown as error:
        raise ToolError(f'daemon_down: nxagentd is not running and could not be started: {error}')

    if response.get('ok'):
        return response.get('result')

    kind = response.get('kind', 'device_error')
    detail = {k: v for k, v in response.items() if k not in ('ok', 'kind', 'error')}

    raise ToolError(f"{kind}: {response.get('error')}. {HINTS.get(kind, '')} {json.dumps(detail, default=str)}")


async def acall(cmd, **args):
    return await anyio.to_thread.run_sync(lambda: call(cmd, **args))


def shrink(data, fmt, max_width):
    if not max_width or max_width >= 1280:
        return data, fmt

    from PIL import Image as Pil

    image = Pil.open(io.BytesIO(data)).convert('RGB')
    image.thumbnail((max_width, max_width * 9 // 16 + 1))
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=85)

    return out.getvalue(), 'jpeg'


async def nx_status(live: bool = True) -> dict:
    """Console state (absent/hos/hekate_remote/mtp/ums), Worker version and features, app, session, guard.
    Always call this first."""
    return await acall('status', live=live)


async def nx_wait_for_state(state: str, timeout_s: int = 60) -> dict:
    """Wait until the console reaches a state: hos, hekate_remote, mtp, ums or absent (timeout max 300 s)."""
    return await acall('wait_state', state=state, timeout=timeout_s)


async def nx_screenshot(max_width: int = 1280) -> Image:
    """Capture the screen. In HOS: JPEG of the composited screen including Tesla overlays.
    In Remote Hekate: PNG of the Nyx framebuffer. Use max_width (e.g. 640) to save tokens."""
    status = await acall('status', live=False)
    hekate = str(status.get('state', '')).startswith('hekate_remote')
    suffix = '.png' if hekate else '.jpg'

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / f'screen{suffix}')
        await acall('hekate.screen' if hekate else 'screen', path=path)
        data = Path(path).read_bytes()

    data, fmt = shrink(data, 'png' if hekate else 'jpeg', max_width)

    return Image(data=data, format=fmt)


async def nx_current_app() -> dict:
    """Title ID and pid of the running application (0000000000000000 = HOME menu)."""
    return await acall('app')


async def nx_list_dir(path: str) -> list:
    """List an SD directory, e.g. sdmc:/atmosphere/contents."""
    return await acall('ls', remote=path)


async def nx_read_file(path: str, max_bytes: int = 1048576) -> dict:
    """Read an SD file (keys, saves, emuMMC and Nintendo folders are refused by the console).
    Returns text when it is UTF-8, otherwise base64."""
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / 'file'
        meta = await acall('get', remote=path, local=str(local))
        data = local.read_bytes()

    if len(data) > max_bytes:
        raise ToolError(f'invalid: file is {len(data)} bytes, above max_bytes={max_bytes}; use nx_download_file')

    try:
        return {'path': path, 'bytes': len(data), 'text': data.decode('utf-8'), **(meta or {})}
    except UnicodeDecodeError:
        return {'path': path, 'bytes': len(data), 'base64': base64.b64encode(data).decode(), **(meta or {})}


async def nx_download_file(path: str, local_path: str) -> dict:
    """Copy an SD file to a local path on the host."""
    return await acall('get', remote=path, local=str(Path(local_path).expanduser().resolve()))


async def nx_events(limit: int = 50) -> list:
    """Recent daemon events (state changes, retries, jobs)."""
    path = Path(EVENTS_PATH)

    if not path.exists():
        return []

    return [json.loads(line) for line in path.read_text().splitlines()[-limit:]]


async def nx_hekate_status() -> dict:
    """Remote Hekate status (only in state hekate_remote)."""
    return await acall('hekate.status')


async def nx_hekate_entries() -> list:
    """Boot entries listed by Remote Hekate."""
    return await acall('hekate.entries')


async def nx_session_status() -> dict:
    """Sleep-lease/controller session status."""
    return await acall('session.status')


async def nx_sleep_settings() -> dict:
    """Current HOS sleep settings."""
    return await acall('sleep_settings')


async def nx_collect_logs(local_dir: str) -> dict:
    """Copy crash, fatal, erpt and binlog reports from the SD to a local directory."""
    return await acall('logs.collect', local=str(Path(local_dir).expanduser().resolve()))


async def nx_job_status(job_id: str = '') -> dict:
    """Status of a long job started by nx_sd_put_offline or nx_console_update."""
    return await acall('job.status', job_id=job_id or None)


async def nx_press(buttons: str, ms: int = 120, lx: int = 0, ly: int = 0, rx: int = 0, ry: int = 0) -> dict:
    """Press buttons, e.g. 'A', 'HOME', 'ZL+ZR'. Sticks range -32767..32767. Take a screenshot afterwards."""
    return await acall('press', buttons=buttons, ms=ms, sticks=[lx, ly, rx, ry])


async def nx_hold(buttons: str, ms: int) -> dict:
    """Hold buttons for ms milliseconds (max 30000)."""
    return await acall('hold', buttons=buttons, ms=ms)


async def nx_sequence(steps: list[dict], wait: bool = True) -> dict:
    """Run up to 64 timed steps on the console: [{"buttons":"A","ms":120}, {"buttons":"","ms":2000,"lx":30000}].
    Empty buttons with stick values walks a character."""
    return await acall('seq', steps=steps, wait=wait)


async def nx_controller(action: str, mode: str = 'rail') -> dict:
    """attach or detach the virtual controller (mode rail or fullkey)."""
    if action not in ('attach', 'detach'):
        raise ToolError('invalid: action must be attach or detach')

    return await acall(action, **({'mode': mode} if action == 'attach' else {}))


async def nx_session_start(minutes: int, mode: str = 'rail') -> dict:
    """Keep the console awake and the controller attached for N minutes (sleep lease)."""
    return await acall('session.start', minutes=minutes, mode=mode)


async def nx_session_stop() -> dict:
    """End the session and restore sleep settings."""
    return await acall('session.stop')


async def nx_hekate_tap(x: int, y: int, ms: int = 150) -> dict:
    """Tap the Remote Hekate touch screen at (x, y) in 1280x720 coordinates."""
    return await acall('hekate.tap', x=x, y=y, ms=ms)


async def nx_hekate_drag(x1: int, y1: int, x2: int, y2: int) -> dict:
    """Drag on the Remote Hekate touch screen."""
    return await acall('hekate.drag', x1=x1, y1=y1, x2=x2, y2=y2)


async def nx_hekate_watchdog(seconds: int) -> dict:
    """Set the Remote Hekate watchdog: continue booting HOS after N seconds without host commands (0 disables)."""
    return await acall('hekate.watchdog', seconds=seconds)


async def nx_write_file(path: str, content: str = '', local_path: str = '', create: bool = False,
                        content_base64: str = '') -> dict:
    """Write an SD file through the Worker (allow-listed paths only: atmosphere/config and config/4IFIR *.ini,
    hekate_ipl.ini, boot2.flag, ...). Transactional with readback; journaled on the host."""
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(local_path).expanduser().resolve() if local_path else Path(tmp) / 'content'

        if not local_path:
            local.write_bytes(base64.b64decode(content_base64) if content_base64 else content.encode())

        return await acall('put', remote=path, local=str(local), create=create)


async def nx_set_bootflag(tid: str, enable: bool) -> dict:
    """Enable or disable a sysmodule boot2.flag. Guarded: Rescue reverts it if the next boots are not confirmed."""
    return await acall('bootflag', tid=tid, enable=enable)


async def nx_guard_confirm() -> dict:
    """Confirm a healthy boot after a guarded change."""
    return await acall('guard.confirm')


async def nx_reboot(target: str = 'hos', entry: int = 0, wait: bool = True) -> dict:
    """Reboot. target: hos (plain reboot), remote (Remote Hekate), ums (hekate UMS), menu (hekate menu),
    entry (hekate boot entry 1..8). Refused within 60 s after boot."""
    if target == 'hos':
        return await acall('reboot')

    return await acall('reboot_to', target=target, entry=entry or None, wait=wait)


async def nx_hekate_action(action: str) -> dict:
    """In Remote Hekate: launch (continue boot to HOS), ums, reboot, poweroff."""
    if action not in ('launch', 'ums', 'reboot', 'poweroff'):
        raise ToolError('invalid: action must be launch, ums, reboot or poweroff')

    return await acall('hekate.action', action=action)


async def nx_sd_put_offline(files: list[dict], hooks: list[str] | None = None) -> dict:
    """Copy local files anywhere on the SD (including /switch and kips) via Remote Hekate UMS, then boot back to HOS.
    files: [{"local": "/path", "remote": "switch/app/config.ini"}]. Returns a job id; poll nx_job_status.
    Closes the running game."""
    files = [{'remote': f['remote'], 'local': str(Path(f['local']).expanduser().resolve())} for f in files]

    return await acall('ums.put', files=files, hooks=hooks or [])


async def nx_console_update(action: str, sha256: str = '', image: str = '', fallback_sha256: str = '',
                            device_tested: bool = False) -> dict:
    """Worker sysmodule maintenance through Rescue: status, install (needs known-good fallback sha256),
    promote (after on-device testing), rollback."""
    return await acall('console.update', action=action, sha256=sha256 or None,
                       image=str(Path(image).expanduser().resolve()) if image else None,
                       fallback_sha256=fallback_sha256 or None, device_tested=device_tested)


RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)

TOOLS = {
    'readonly': [(f, RO) for f in (nx_status, nx_wait_for_state, nx_screenshot, nx_current_app, nx_list_dir,
                                   nx_read_file, nx_download_file, nx_events, nx_hekate_status, nx_hekate_entries,
                                   nx_session_status, nx_sleep_settings, nx_collect_logs, nx_job_status)],
    'interactive': [(f, ACT) for f in (nx_press, nx_hold, nx_sequence, nx_controller, nx_session_start,
                                       nx_session_stop, nx_hekate_tap, nx_hekate_drag, nx_hekate_watchdog)],
    'full': [(f, DESTRUCTIVE) for f in (nx_write_file, nx_set_bootflag, nx_guard_confirm, nx_reboot,
                                        nx_hekate_action, nx_sd_put_offline, nx_console_update)],
}


def build_server(profile='interactive'):
    if profile not in PROFILES:
        raise ValueError(f'profile must be one of {PROFILES}')

    server = MCPServer('nx-agent', instructions=data_text('SAFETY.md'))

    for level in PROFILES[:PROFILES.index(profile) + 1]:
        for fn, annotations in TOOLS[level]:
            server.add_tool(fn, name=fn.__name__, description=fn.__doc__, annotations=annotations)

    @server.resource('nxagent://protocol', mime_type='text/markdown')
    def protocol() -> str:
        """Wire protocol of Worker, Rescue and Remote Hekate."""
        return data_text('PROTOCOL.md')

    @server.resource('nxagent://safety', mime_type='text/markdown')
    def safety() -> str:
        """Safety rules for driving a real console."""
        return data_text('SAFETY.md')

    @server.resource('nxagent://buttons', mime_type='application/json')
    def buttons() -> str:
        """Button names accepted by nx_press and nx_sequence."""
        from .proto.worker import BUTTONS

        return json.dumps(sorted(BUTTONS))

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default=os.environ.get('NXAGENT_MCP_PROFILE', 'interactive'), choices=PROFILES)
    parser.add_argument('--transport', choices=('stdio', 'streamable-http'), default='stdio')
    parser.add_argument('--host', default='127.0.0.1', help='bind address for streamable-http')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--path', default=os.environ.get('NXAGENT_MCP_PATH', '/mcp'),
                        help='HTTP endpoint path; use an unguessable one (e.g. /mcp-<random>) behind a public tunnel')
    parser.add_argument('--allowed-host', action='append', default=[], metavar='HOST',
                        help='extra Host header accepted (e.g. the tunnel domain); repeatable')
    args = parser.parse_args(argv)
    server = build_server(args.profile)

    if args.transport == 'stdio':
        server.run('stdio')

        return

    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ['127.0.0.1', '127.0.0.1:*', 'localhost', 'localhost:*', *args.allowed_host]
    server.run('streamable-http', host=args.host, port=args.port, stateless_http=True, streamable_http_path=args.path,
               transport_security=TransportSecuritySettings(allowed_hosts=hosts))


if __name__ == '__main__':
    sys.exit(main())
