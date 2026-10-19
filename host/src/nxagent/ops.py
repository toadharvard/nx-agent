"""Device operations on top of DaemonCore. Every mutation passes a read-only verifier."""
import hashlib
import json
import os
import struct
import time
from pathlib import Path

from .common import HOS, NYX, OpError, console_error, feature_names
from .core import DaemonCore, decode_state
from .features import FLAG, Guard, Session, normalize_tid
from .jobs import Jobs
from .links import build_mailbox_request

HEKATE_INI = 'sdmc:/bootloader/hekate_ipl.ini'
REMOTE_ENTRY = 'Remote USB Control'
R2C_REASON = {'entry': 1, 'remote': 1, 'menu': 2, 'ums': 3}
MAILBOX_LEN = {40: (0, 1), 41: (28,), 42: (0,), 45: (0,), 48: (0,), 49: (0,), 54: (0,), 50: (4,),
               52: (32, 60)}
FB = (1280, 720, 4, 1280 * 720 * 4)


def parse_buttons(spec):
    from .proto.worker import BUTTONS

    mask = 0

    for name in filter(None, str(spec).upper().split('+')):
        if name not in BUTTONS:
            raise OpError('invalid', f'Unknown button {name}; known: {"+".join(BUTTONS)}')

        mask |= BUTTONS[name]

    return mask


def pack_step(buttons, ms, sticks=(0, 0, 0, 0), max_ms=30000):
    if not 1 <= int(ms) <= max_ms:
        raise OpError('invalid', f'Step duration must be 1..{max_ms} ms')

    if any(not -32767 <= int(value) <= 32767 for value in sticks):
        raise OpError('invalid', 'Stick values must be -32767..32767')

    return struct.pack('<Q4iI', parse_buttons(buttons), *map(int, sticks), int(ms))


def hekate_entry_index(text, name=REMOTE_ENTRY):
    index = 0

    for line in text.splitlines():
        line = line.strip()

        if not line.startswith('[') or ']' not in line:
            continue

        section = line[1:line.index(']')]

        if section == 'config':
            continue

        index += 1

        if section == name:
            return index

    return None


def save_frame(path, size, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image
    except ImportError:
        raw = path.with_suffix('.bgrx')
        raw.write_bytes(data)
        raw.with_suffix('.json').write_text(json.dumps({'width': size[0], 'height': size[1], 'format': 'BGRX'}))

        return {'image': str(raw), 'format': 'raw BGRX (PIL unavailable)'}

    Image.frombytes('RGB', size, data, 'raw', 'BGRX').save(path)

    return {'image': str(path), 'format': 'png'}


class Daemon(DaemonCore):
    def __init__(self, links, **kwargs):
        super().__init__(links, **kwargs)
        self.nyx_cache = {}
        self.session = Session(self)
        self.guard = Guard(self)
        self.jobs = Jobs(self.events)

    # ---- long jobs and waits ------------------------------------------------
    def wait_state(self, state, timeout=60):
        if state not in ('hos', 'hekate_remote', 'mtp', 'ums', 'absent'):
            raise OpError('invalid', f'Unknown state {state}')

        timeout = min(float(timeout), 300)
        start = self.clock.now()
        reached = self.wait_until(lambda: (self.present(), self.monitor.state)[1] == state, timeout)

        return {'reached': reached, 'state': self.monitor.state, 'elapsed_s': round(self.clock.now() - start, 1)}

    def ums_put(self, files, hooks=(), wait=False):
        from .workflows.ums import ums_put

        if wait:
            return ums_put(self, files, hooks)

        return self.jobs.start('ums.put', lambda step: ums_put(self, files, hooks, step=step))

    def console_update(self, action, sha256=None, image=None, fallback_sha256=None, device_tested=False):
        from .workflows.console import console_update

        if action == 'status':
            return console_update(self, 'status')

        return self.jobs.start(f'console.{action}', lambda step: console_update(
            self, action, sha256, image, fallback_sha256, device_tested, step=step))

    # ---- capability helpers -------------------------------------------------
    def capabilities(self):
        return self.run('worker', 'capabilities', (6, 9), lambda c: c.caps)

    def worker_has(self, bit):
        try:
            caps = self.capabilities()
        except OpError:
            return False

        return caps[0] >= 7 and bool(caps[5] & bit)

    def require(self, bit, label):
        caps = self.capabilities()

        if caps[0] < 7 or not caps[5] & bit:
            raise OpError('device_error', f'Worker v{caps[0]} does not support {label}', unsupported=True)

    def mtp_mode(self):
        return self.monitor.state == 'mtp'

    # ---- read-only state ----------------------------------------------------
    def read_state(self):
        def body(conn):
            extended = conn.version >= 7 and conn.caps[5] & 32

            return decode_state(conn.cmd(54 if extended else 48))

        return self.run('worker', 'controller state', (48, 54), body)

    def app(self):
        return self.run('worker', 'app', (45,), lambda c: c.sidecar.app())

    def health(self):
        try:
            app = self.app()
        except OpError as error:
            return {'healthy': False, 'error': error.to_dict()}

        return {'healthy': app['query_succeeded'], 'app': app}

    def sleep_settings(self):
        def body(conn):
            flags, handheld, console, active = struct.unpack_from('<IiiI', conn.cmd(49))
            result = {'flags': flags, 'handheld_plan': handheld, 'console_plan': console,
                      'lease_active': bool(active)}

            if conn.version >= 7 and conn.caps[5] & 32:
                result['state'] = decode_state(conn.cmd(54))

            return result

        return self.run('worker', 'sleep settings', (49, 54), body)

    def ls(self, remote):
        if self.mtp_mode():
            return self.on_usb(self.links.mtp.ls, remote)

        return self.run('worker', 'list', (16,), lambda c: c.sidecar.list(remote))

    def get(self, remote, local, limit=64 * 1024 * 1024):
        if self.mtp_mode():
            return self.on_usb(self.links.mtp.get, remote, local)

        data = self.run('worker', 'get', (17,), lambda c: c.sidecar.get(remote, limit))
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(data)

        return {'remote': remote, 'local': str(local), 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}

    def collect(self, local):
        return self.run('worker', 'logs collect', (16, 17), lambda c: c.sidecar.collect(local))

    def screen(self, local, layer=None):
        if self.monitor.state == 'hekate_remote':
            return self.nyx_screen(local)

        return self.run('worker', 'screenshot', (43, 44), lambda c: c.sidecar.screenshot(local, layer))

    # ---- verifiers (read-only) ---------------------------------------------
    def verify_lease(self, seconds):
        state = self.sleep_settings()
        ok = state['lease_active'] == (seconds > 0)

        return {'outcome': 'applied' if ok else 'not_applied', 'lease_active': state['lease_active']}

    def verify_controller(self, expect=None):
        state = self.read_state()

        if expect is None:
            return {'outcome': 'unknown', 'state': state}

        ok = bool(state['pad_ready']) == (expect == 'attached')

        return {'outcome': 'applied' if ok else 'not_applied', 'state': state}

    def verify_file(self, remote, sha):
        size, digest = self.run('worker', 'fingerprint', (18,), lambda c: c.sidecar.fingerprint(remote))
        ok = digest.hex() == sha

        return {'outcome': 'applied' if ok else 'unknown', 'remote_size': size, 'remote_sha256': digest.hex()}

    def verify_flag(self, tid, enable):
        names = sorted(item['name'] for item in self.ls(FLAG.format(tid).rsplit('/', 1)[0]))
        ok = ('boot2.flag' in names) == bool(enable)

        return {'outcome': 'applied' if ok else 'not_applied', 'files': names}

    # ---- mutations ----------------------------------------------------------
    def worker_op(self, name, op, data=b'', verify=None, decode=None):
        def body(conn):
            payload = conn.cmd(op, data)

            return decode(payload) if decode else {'done': True}

        return self.run('worker', name, (op,), body, verify)

    def autohold(self, enable):
        result = self.worker_op('autohold', 56, bytes([1 if enable else 0]))

        if not enable:
            result['lease_released'] = self.sleep_lease(0)

        return {'autohold': bool(enable), **result}

    def sleep_lease(self, seconds):
        seconds = int(seconds)

        if seconds and not 15 <= seconds <= 600:
            raise OpError('invalid', 'Lease must be 0 or 15..600 seconds')

        data = struct.pack('<I', seconds)

        if self.mtp_mode():
            return self.mailbox_call('sleep lease', 50, data)

        result = self.worker_op('sleep lease', 50, data, verify=lambda: self.verify_lease(seconds))

        return {'lease_seconds': seconds, **result}

    def wake_alarm(self, seconds):
        seconds = int(seconds)

        if seconds and not 60 <= seconds <= 3600:
            raise OpError('invalid', 'Wake alarm must be 0 or 60..3600 seconds')

        self.require(8, 'wake alarm (op55)')

        def verify():
            state = self.read_state()

            return {'outcome': 'applied' if state.get('wake_alarm_s') == seconds else 'not_applied', 'state': state}

        return {'wake_alarm_s': seconds, **self.worker_op('wake alarm', 55, struct.pack('<I', seconds), verify)}

    def attach(self, mode='rail'):
        if mode not in ('rail', 'fullkey'):
            raise OpError('invalid', 'Controller mode must be rail or fullkey')

        data = b'\2' if mode == 'fullkey' else b''

        if self.mtp_mode():
            return self.mailbox_call('attach', 40, data)

        def decode(payload):
            if len(payload) < 8:
                return {'requested': mode}

            active, fullkey_result = struct.unpack_from('<II', payload)

            return {'requested': mode, 'mode_active': 'fullkey' if active == 2 else 'rail',
                    'fullkey_result': f'0x{fullkey_result:x}'}

        return self.worker_op('attach', 40, data, lambda: self.verify_controller('attached'), decode)

    def detach(self):
        if self.mtp_mode():
            return self.mailbox_call('detach', 42)

        return self.worker_op('detach', 42, verify=lambda: self.verify_controller('detached'))

    def press(self, buttons, ms=120, sticks=(0, 0, 0, 0)):
        packet = pack_step(buttons, ms, sticks, max_ms=1000)

        if self.mtp_mode():
            return {'pressed': buttons, **self.mailbox_call('press', 41, packet)}

        def body(conn):
            conn.cmd(41, packet)
            self.clock.sleep(int(ms) / 1000 + 0.05)

            return {'pressed': buttons, 'ms': int(ms)}

        result = self.run('worker', 'press', (41,), body, verify=self.verify_controller)

        try:
            result['controller'] = self.read_state()
        except OpError as error:
            result['controller_error'] = error.to_dict()

        return result

    def sequence(self, steps, wait=True):
        if not 1 <= len(steps) <= 64:
            raise OpError('invalid', 'Sequence needs 1..64 steps')

        packed = [pack_step(s.get('buttons', ''), s.get('ms', 100),
                            [s.get(k, 0) for k in ('lx', 'ly', 'rx', 'ry')]) for s in steps]
        total = sum(int(s.get('ms', 100)) for s in steps)

        if total > 60000:
            raise OpError('invalid', 'Sequence total duration must be <= 60000 ms')

        data = struct.pack('<I', len(packed)) + b''.join(packed)

        if self.mtp_mode():
            if len(packed) > 2:
                raise OpError('invalid', 'Mailbox sequences are limited to 2 steps')

            return self.mailbox_call('sequence', 52, data)

        self.require(2, 'sequences (op52)')
        result = {'steps': len(packed), 'total_ms': total,
                  **self.worker_op('sequence', 52, data, verify=self.verify_controller)}

        if wait:
            finished = self.wait_until(lambda: not self.read_state().get('seq_active'), total / 1000 + 3, 0.25)
            result['finished'] = finished

        return result

    def put(self, remote, local, create=False):
        if self.mtp_mode():
            return self.on_usb(self.links.mtp.put, remote, local, create)

        content = Path(local).read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        started = time.time_ns()
        result = self.run('worker', 'put', (17, 32, 33, 34, 18),
                          lambda c: c.sidecar.put(remote, content, create),
                          verify=lambda: self.verify_file(remote, sha))

        if result.get('reply_lost'):
            result['journal'] = self._journal_since(started, remote)

        return result

    def require_mtp(self, label):
        if not self.mtp_mode():
            raise OpError('invalid', f'{label} needs DBI MTP mode (start "Run MTP responder" in DBI); '
                                     f'in HOS use put, or ums-put for paths outside the Worker allow-list')

    def mv(self, source, dest):
        self.require_mtp('mv')

        return self.on_usb(self.links.mtp.mv, source, dest)

    def rm(self, remote, recursive=False):
        self.require_mtp('rm')

        return self.on_usb(self.links.mtp.rm, remote, recursive)

    def mkdir(self, remote, parents=False):
        self.require_mtp('mkdir')

        return self.on_usb(self.links.mtp.mkdir, remote, parents)

    def _journal_since(self, started, remote):
        from . import paths

        for run in sorted(paths.journal_dir().glob('sidecar-change-*'), reverse=True):
            if int(run.name.rsplit('-', 1)[1]) < started:
                break

            if json.loads((run / 'manifest.json').read_text()).get('target') == remote:
                return str(run)

        return None

    def bootflag(self, tid, enable, unguarded=False, guard_revert=False):
        tid = normalize_tid(tid)

        if not (unguarded or guard_revert or self.guard.covers(tid, enable)):
            raise OpError('invalid', 'Arm the boot guard for this flag first (guard arm), or pass --unguarded')

        result = self.worker_op('bootflag', 47, bytes([bool(enable)]) + tid.encode(),
                                verify=lambda: self.verify_flag(tid, enable))
        result.update(title=tid, state='enable' if enable else 'disable', effective='next_boot')

        if not result.get('reply_lost'):
            result['check'] = self.verify_flag(tid, enable)

        return result

    def check_uptime(self, force=False):
        limit = float(self.config.get('min_uptime_before_reboot_s', 60))
        uptime = self.monitor.uptime(HOS)

        if force or uptime is None or uptime >= limit:
            return

        raise OpError('invalid', f'HOS has been up only {uptime:.0f}s; wait until {limit:.0f}s before rebooting '
                      '(quick reboots can trip boot watchdogs such as 4IFIR Safe-Mode)', uptime_s=round(uptime))

    def reboot(self, force=False):
        self.check_uptime(force)
        result = self.worker_op('reboot', 46, b'REBOOT', verify=lambda: self.verify_disappear(HOS))
        result['disappeared'] = self.wait_until(lambda: HOS not in self.present(), 20)

        return result

    # ---- R2C ----------------------------------------------------------------
    def remote_entry_index(self):
        if self.config.get('remote_entry_index'):
            return int(self.config['remote_entry_index'])

        cache = self.state_dir / 'hekate_ipl.ini'

        try:
            text = self.run('worker', 'read hekate_ipl.ini', (17,), lambda c: c.sidecar.get(HEKATE_INI, 256 * 1024))
            cache.write_bytes(text)
        except OpError:
            if not cache.exists():
                raise

            text = cache.read_bytes()

        index = hekate_entry_index(text.decode('utf-8', 'replace'), self.config.get('remote_entry_name', REMOTE_ENTRY))

        if index is None:
            raise OpError('device_error', f'[{REMOTE_ENTRY}] not found in hekate_ipl.ini')

        return index

    def reboot_to(self, target, entry=None, wait=True, timeout=120, force=False):
        if target not in R2C_REASON:
            raise OpError('invalid', 'Target must be ums, remote, menu or entry')

        self.check_uptime(force)

        if self.monitor.state != 'hos':
            raise OpError('absent', 'R2C needs HOS with Worker running')

        self.require(4, 'R2C (op53)')
        index = {'entry': entry, 'remote': None, 'menu': 0, 'ums': 0}[target]

        if target == 'remote':
            index = self.remote_entry_index()

        if R2C_REASON[target] == 1 and not (index and 1 <= int(index) <= 8):
            raise OpError('invalid', 'Boot entry index must be 1..8')

        disks = self.links.external_disks() if target == 'ums' else set()
        self.monitor.expect_ums = target == 'ums'
        data = b'R2C' + bytes([R2C_REASON[target], int(index), 1])

        try:
            result = self.worker_op(f'reboot-to {target}', 53, data, verify=lambda: self.verify_disappear(HOS))
        except OpError:
            self.monitor.expect_ums = False
            raise

        out = {'target': target, 'reason': R2C_REASON[target], 'index': int(index), **result}
        out['disappeared'] = self.wait_until(lambda: HOS not in self.present(), 20)

        if not out['disappeared']:
            raise OpError('device_error', 'R2C accepted but the console did not leave USB', **out)

        return self._wait_target(target, out, disks, wait, timeout)

    def _wait_target(self, target, out, disks, wait, timeout):
        if not wait or target not in ('remote', 'ums'):
            return out

        if target == 'remote':
            out['reached'] = self.wait_until(lambda: NYX in self.present(), timeout)
        else:
            found = []

            def disk_appeared():
                self.present()
                found[:] = sorted(self.links.external_disks() - disks)

                return bool(found)

            out['reached'] = self.wait_until(disk_appeared, timeout, interval=2)
            out['ums_disks'] = found

        if not out['reached']:
            raise OpError('absent', f'{target} did not appear within {timeout}s', **out)

        return out

    # ---- Remote Hekate -------------------------------------------------------
    def nyx_version(self):
        generation = self.monitor.generation[NYX]

        if self.nyx_cache.get('gen') != generation:
            try:
                version = self.run('nyx', 'status', (12,), lambda c: struct.unpack_from('<4I', c.call(12))[0])
            except OpError as error:
                if error.kind != 'device_error':
                    raise

                version = 1

            self.nyx_cache = {'gen': generation, 'version': version}

        return self.nyx_cache['version']

    def nyx_require_v2(self, label):
        version = self.nyx_version()

        if version < 2:
            raise OpError('device_error', f'Remote Hekate v{version} has no {label}; install v2', unsupported=True)

    def nyx_screen(self, local):
        def body(conn):
            info = struct.unpack_from('<6I', conn.call(1))

            if tuple(info[1:5]) != FB:
                raise OpError('device_error', f'Unknown framebuffer format {info}')

            generation = struct.unpack_from('<I', conn.call(2))[0]
            data = bytearray()

            for offset in range(0, FB[3], 4064):
                data.extend(conn.call(3, generation, offset, min(4064, FB[3] - offset)))

            if len(data) != FB[3]:
                raise OpError('uncertain', 'Incomplete framebuffer')

            return bytes(data)

        return save_frame(local, FB[:2], self.run('nyx', 'screen', (1, 2, 3), body))

    def nyx_tap(self, x, y, ms=150):
        self.run('nyx', 'tap', (4,), lambda c: c.call(4, int(x), int(y), int(ms)))

        return {'tap': [x, y], 'ms': ms}

    def nyx_release(self):
        self.run('nyx', 'release', (5,), lambda c: c.call(5))

        return {'released': True}

    def nyx_drag(self, x1, y1, x2, y2):
        def body(conn):
            for i in range(21):
                conn.call(4, round(x1 + (x2 - x1) * i / 20), round(y1 + (y2 - y1) * i / 20), 250)
                self.clock.sleep(0.03)

            conn.call(5)

            return {'drag': [x1, y1, x2, y2]}

        return self.run('nyx', 'drag', (4, 5), body)

    def nyx_entries(self):
        self.nyx_require_v2('entries (op6)')
        text = self.run('nyx', 'entries', (6,), lambda c: c.call(6)).decode('utf-8', 'replace')
        entries = []

        for line in text.splitlines():
            if '\t' in line:
                index, name = line.split('\t', 1)
                entries.append({'index': int(index), 'name': name})

        return entries

    def nyx_status(self):
        if self.nyx_version() >= 2:
            fields = ('version', 'watchdog_s_left', 'watchdog_enabled', 'host_commands')
            payload = self.run('nyx', 'status', (12,), lambda c: c.call(12))

            return dict(zip(fields, struct.unpack_from('<4I', payload)))

        info = self.run('nyx', 'info', (1,), lambda c: struct.unpack_from('<6I', c.call(1)))

        return {'version': 1, 'framebuffer': list(info[1:5])}

    def nyx_watchdog(self, seconds):
        seconds = int(seconds)

        if seconds and not 30 <= seconds <= 3600:
            raise OpError('invalid', 'Watchdog must be 0 or 30..3600 seconds')

        self.nyx_require_v2('watchdog (op11)')

        def verify():
            state = self.nyx_status()

            return {'outcome': 'applied' if bool(state['watchdog_enabled']) == bool(seconds) else 'not_applied',
                    'status': state}

        self.run('nyx', 'watchdog', (11,), lambda c: c.call(11, seconds), verify)

        return self.nyx_status()

    def nyx_action(self, action, wait=True, timeout=180):
        op = {'launch': 7, 'ums': 8, 'reboot': 9, 'poweroff': 10}[action]
        self.nyx_require_v2(f'{action} (op{op})')
        disks = self.links.external_disks() if action == 'ums' else set()
        self.monitor.expect_ums = action == 'ums'
        result = self.run('nyx', action, (op,), lambda c: {'done': (c.call(op, 1) if op == 9 else c.call(op)) is not None},
                          verify=lambda: self.verify_disappear(NYX, 15))
        out = {'action': action, **result, 'disappeared': self.wait_until(lambda: NYX not in self.present(), 15)}

        if action == 'launch' and wait:
            out['hos_appeared'] = self.wait_until(lambda: HOS in self.present(), timeout)
        elif action == 'ums':
            out = self._wait_target('ums', out, disks, wait, timeout)

        return out

    # ---- MTP mailbox ---------------------------------------------------------
    def mailbox_call(self, name, op, data=b''):
        if op not in MAILBOX_LEN:
            raise OpError('invalid', f'op{op} is not allowed through the MTP mailbox')

        if len(data) not in MAILBOX_LEN[op]:
            raise OpError('invalid', f'op{op}: payload length {len(data)} not allowed through the mailbox')

        return self.on_usb(self._mailbox_call, name, op, data)

    def _mailbox_call(self, name, op, data):
        box = self.links.mailbox
        status = box.read_status()

        if status.get('phase') == 'claimed':
            raise OpError('uncertain', 'Previous mailbox command is still claimed; inspect before sending another',
                          mailbox=status)

        sequence = int(status['sequence']) + 1
        request = build_mailbox_request(status['token'], sequence, int(status['now_ms']), op, data)
        journal = self.state_dir / 'mailbox' / f'{time.time_ns()}.json'
        journal.parent.mkdir(parents=True, exist_ok=True)
        record = {'op': op, 'name': name, 'sequence': sequence, 'token': status['token'], 'status': 'sending_once'}
        journal.write_text(json.dumps(record))
        self.events.emit('mailbox_submit', op=op, sequence=sequence)
        record['submit_rc'] = box.submit(request)
        outcome = self._mailbox_outcome(box, status['token'], sequence)
        record.update(status=outcome['outcome'], outcome=outcome)
        journal.write_text(json.dumps(record))

        if outcome['outcome'] != 'applied':
            raise OpError('uncertain', f'Mailbox {name} not confirmed; never resent', verification=outcome,
                          replayed=False, journal=str(journal))

        if outcome['result']:
            raise console_error(outcome['result'], 'mailbox', op)

        return {'via': 'mtp_mailbox', 'sequence': sequence, 'reply': outcome['reply']}

    def _mailbox_outcome(self, box, token, sequence, timeout=25):
        seen = {}

        def settled():
            try:
                seen['status'] = box.read_status()
            except OpError:
                return False

            status = seen['status']

            return status.get('token') != token or (int(status['sequence']) >= sequence and status['phase'] == 'done')

        self.wait_until(settled, timeout, interval=1)
        status = seen.get('status')

        if status is None:
            return {'outcome': 'unknown', 'reason': 'mailbox status unreadable'}

        if status.get('token') != token:
            return {'outcome': 'unknown', 'reason': 'Worker restarted (token changed)'}

        if int(status['sequence']) >= sequence and status['phase'] == 'done':
            return {'outcome': 'applied', 'result': int(status['result']), 'reply': status.get('reply', '')}

        if int(status['sequence']) < sequence:
            return {'outcome': 'not_applied', 'reason': 'request not claimed before its deadline'}

        return {'outcome': 'unknown', 'status': status}

    def mtp_status(self):
        return self.on_usb(self.links.mailbox.read_status)

    # ---- status --------------------------------------------------------------
    def status(self, live=True):
        monitor = self.monitor
        state = monitor.state
        caps = self.worker_caps
        out = {'state': f'hos(worker {self.worker_state})' if state == 'hos' else state,
               'devices': sorted(monitor.present), 'generation': dict(monitor.generation),
               'worker_version': caps[0] if caps else None,
               'worker_features': feature_names(caps[5]) if caps and caps[0] >= 7 else [],
               'rescue_version': self.rescue_version, 'uncertain_streams': [t for t, v in self.dirty.items() if v],
               'session': self.session.status(), 'guard': self.guard.load(), 'pid': os.getpid()}

        if not live:
            return out

        try:
            if state == 'hos':
                out['live'] = {'app': self.app(), 'controller': self.read_state()}
                out['state'] = f'hos(worker {self.worker_state})'
            elif state == 'hekate_remote':
                out['live'] = self.nyx_status()
        except OpError as error:
            out['live_error'] = error.to_dict()
            out['state'] = f'hos(worker {self.worker_state})' if state == 'hos' else state

        return out
