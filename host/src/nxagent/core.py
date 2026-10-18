"""Daemon core: device monitor, single USB executor, retry and verification policy."""
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from .common import (HOS, MAX_READ_RETRIES, MTP, NYX, STATE_DIR, EventLog, OpError, classify,
                    console_error, is_read_only, load_config)

USB_THREAD = 'nxagentd-usb'
WORKER_START_INTERVAL = 30
WORKER_START_WAIT = 20
RESCUE_ROLE = 0x52534355

STATE54 = ('layout', 'mode_active', 'pad_ready', 'seq_active', 'seq_index', 'seq_count',
           'last_control_result', 'pad_idle_ms_left', 'lease_active', 'wake_alarm_s',
           'last_wake_reason', 'r2c_pending')
STATE48 = ('pad_ready', 'release_pending', 'last_control_result', 'reboot_pending')


def decode_state(payload):
    if len(payload) >= 48:
        return dict(zip(STATE54, struct.unpack_from('<12I', payload)))

    return dict(zip(STATE48, struct.unpack_from('<4I', payload)))


class Clock:
    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


class Conn:
    """One open transport for one job. `raw` is a UsbSocket, a Nyx or a test fake."""

    def __init__(self, target, raw):
        self.target = target
        self.raw = raw
        self.version = None
        self.caps = None

    def cmd(self, op, data=b''):
        error, payload, _ = self.raw.command(op, data)

        if error:
            raise console_error(error, self.target, op)

        return payload

    def call(self, op, *args):
        return self.raw.call(op, *args)

    @property
    def sidecar(self):
        from .proto.worker import Sidecar

        client = Sidecar.__new__(Sidecar)
        client.usb = self.raw
        client.capabilities = self.caps

        return client

    @property
    def rescue(self):
        from .proto.rescue import Rescue

        client = Rescue.__new__(Rescue)
        client.usb = self.raw
        client.capabilities = self.caps

        return client

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


class Monitor:
    """Tracks USB presence only by enumeration; never opens a device."""

    def __init__(self, links, events):
        self.links = links
        self.events = events
        self.lock = threading.Lock()
        self.present = set()
        self.generation = {HOS: 0, NYX: 0, MTP: 0}
        self.state = 'absent'
        self.expect_ums = False
        self.on_disappear = []
        self.error = None
        self.appeared_at = {}
        self.polled = False

    def poll(self):
        with self.lock:
            try:
                present = set(self.links.devices())
            except Exception as exc:
                if self.error != str(exc):
                    self.events.emit('monitor_error', error=str(exc))
                    self.error = str(exc)

                return set(self.present)

            self.error = None
            appeared, gone = present - self.present, self.present - present

            for device in sorted(appeared):
                self.generation[device] += 1

                if self.polled:
                    self.appeared_at[device] = time.monotonic()

                self.events.emit('appear', device=device, generation=self.generation[device])

            for device in sorted(gone):
                self.events.emit('disappear', device=device)
                self.appeared_at.pop(device, None)

            if appeared & {HOS, NYX}:
                self.expect_ums = False

            self.present = present
            self.polled = True
            state = self._state()

            if state != self.state:
                self.events.emit('state', old=self.state, new=state)
                self.state = state

        for device in gone:
            for callback in self.on_disappear:
                callback(device)

        return set(present)

    def uptime(self, device):
        since = self.appeared_at.get(device)

        return None if since is None else time.monotonic() - since

    def _state(self):
        if HOS in self.present:
            return 'hos'

        if NYX in self.present:
            return 'hekate_remote'

        if MTP in self.present:
            return 'mtp'

        return 'ums' if self.expect_ums else 'absent'


class DaemonCore:
    def __init__(self, links, clock=None, events=None, state_dir=STATE_DIR, config=None):
        self.links = links
        self.clock = clock or Clock()
        self.events = events or EventLog()
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config = load_config() if config is None else config
        self.monitor = Monitor(links, self.events)
        self.monitor.on_disappear.append(self._forget_stream)
        self.dirty = {'worker': False, 'rescue': False, 'nyx': False}
        self.worker_state = 'unknown'
        self.worker_caps = None
        self.worker_caps_gen = None
        self.rescue_caps = None
        self.rescue_version = None
        self.last_command_count = None
        self.worker_boots = 0
        self.restart_expected = False
        self.last_worker_start = None
        self.queue_wait = 60
        self.pool = ThreadPoolExecutor(1, thread_name_prefix=USB_THREAD)

    def _forget_stream(self, device):
        if device == HOS:
            self.dirty['worker'] = self.dirty['rescue'] = False
            self.worker_state = 'unknown'

        if device == NYX:
            self.dirty['nyx'] = False

    def present(self):
        return self.monitor.poll()

    def wait_until(self, predicate, timeout, interval=0.5):
        deadline = self.clock.now() + timeout

        while True:
            if predicate():
                return True

            if self.clock.now() >= deadline:
                return False

            self.clock.sleep(interval)

    def on_usb(self, fn, *args):
        if threading.current_thread().name.startswith(USB_THREAD):
            return fn(*args)

        future = self.pool.submit(fn, *args)
        done, _ = wait([future], timeout=self.queue_wait)

        if not done and future.cancel():
            raise OpError('busy', f'USB executor busy for more than {self.queue_wait}s; request not sent')

        return future.result()

    def run(self, target, name, ops, body, verify=None):
        return self.on_usb(self._run, target, name, tuple(ops), body, verify)

    def _run(self, target, name, ops, body, verify):
        read_only = is_read_only(target, ops)
        retries = 0

        while True:
            conn = self._connect(target)

            try:
                result = body(conn)

                if getattr(conn.raw, 'faulted', False):
                    raise OpError('uncertain', 'transport fault swallowed inside the operation')
            except Exception as exc:
                error = classify(exc, target)
            else:
                if target == 'worker':
                    self.worker_state = 'up'

                return result
            finally:
                conn.close()

            if error.kind == 'uncertain':
                self.dirty[target] = True

            if error.kind == 'worker_down':
                self.worker_state = 'down'

            if read_only and retries < MAX_READ_RETRIES and error.kind in ('uncertain', 'worker_down'):
                retries += 1
                self.events.emit('retry', target=target, op=name, kind=error.kind, attempt=retries)

                if error.kind == 'worker_down':
                    self.recover_worker()

                continue

            if read_only:
                raise error

            return self._after_failed_mutation(name, error, verify)

    def _after_failed_mutation(self, name, error, verify):
        error.details['replayed'] = False

        if error.kind == 'worker_down' and error.details.get('executed') is False:
            error.details['recovery'] = self._try_recover()
            raise error

        if error.kind not in ('uncertain', 'worker_down'):
            raise error

        if verify is None:
            verification = {'outcome': 'unknown', 'reason': 'no read-only check defined for this op'}
        else:
            try:
                verification = verify()
            except OpError as check_error:
                verification = {'outcome': 'unknown', 'check_error': check_error.to_dict()}

        self.events.emit('uncertain_mutation', op=name, kind=error.kind, verification=verification)

        if verification.get('outcome') == 'applied':
            return {'reply_lost': True, 'verified': True, 'verification': verification}

        error.details['verification'] = verification
        raise OpError('uncertain', f'{name}: reply lost ({error}); outcome {verification.get("outcome")}',
                      **error.details)

    def _try_recover(self):
        try:
            return self.recover_worker()
        except OpError as error:
            return error.to_dict()

    def _connect(self, target, recover=True):
        recovered = False

        for attempt in range(MAX_READ_RETRIES + 1):
            if self.dirty[target]:
                self._drain(target)

            try:
                raw = self.links.open(target)
            except Exception as exc:
                error = classify(exc, target)

                if error.kind == 'uncertain' and attempt < MAX_READ_RETRIES:
                    continue

                raise error

            try:
                return self._handshake(target, raw)
            except Exception as exc:
                Conn(target, raw).close()
                error = classify(exc, target)

            if error.kind == 'uncertain':
                self.dirty[target] = True
                self.events.emit('reconnect', target=target, attempt=attempt + 1, error=str(error))
                continue

            if error.kind == 'worker_down':
                self.worker_state = 'down'

                if recover and not recovered:
                    recovered = True
                    self.recover_worker()
                    continue

            raise error

        raise OpError('uncertain', f'{target}: no clean stream after {MAX_READ_RETRIES} reconnects')

    def _drain(self, target):
        try:
            count = self.links.drain(target)
        except Exception as exc:
            error = classify(exc, target)

            if error.kind == 'uncertain':
                raise OpError('uncertain', f'{target}: late replies did not quiesce: {error}')

            raise error

        self.dirty[target] = False
        self.events.emit('drained', target=target, discarded=count)

    def _handshake(self, target, raw):
        conn = Conn(target, raw)

        if target == 'worker':
            version, _, _, count = struct.unpack_from('<4I', conn.cmd(6))

            if version < 4:
                raise OpError('device_error', f'Worker v{version} is too old')

            restarted = self._note_command_count(count)
            generation = self.monitor.generation[HOS]

            if restarted or self.worker_caps is None or self.worker_caps_gen != generation:
                self.worker_caps = struct.unpack_from('<6I', conn.cmd(9))
                self.worker_caps_gen = generation

            conn.version, conn.caps = version, self.worker_caps
            self.worker_state = 'up'
        elif target == 'rescue':
            version, role, interface, _ = struct.unpack_from('<4I', conn.cmd(6))

            if role != RESCUE_ROLE or interface != 1 or version not in (6, 7):
                raise OpError('device_error', 'Interface 1 is not the independent Rescue module')

            self.rescue_version = version
            self.rescue_caps = struct.unpack_from('<6I', conn.cmd(9))
            conn.version, conn.caps = version, self.rescue_caps

        return conn

    def _note_command_count(self, count):
        last, self.last_command_count = self.last_command_count, count

        if last is None or count > last:
            return False

        if self.restart_expected:
            self.restart_expected = False
            self.events.emit('worker_restart', expected=True)
        else:
            self.worker_boots += 1
            self.events.emit('worker_restart', expected=False, boots=self.worker_boots)

        return True

    def recover_worker(self):
        return self.on_usb(self._recover_worker)

    def _recover_worker(self):
        now = self.clock.now()

        if self.last_worker_start is not None and now - self.last_worker_start < WORKER_START_INTERVAL:
            raise OpError('worker_down', 'Worker is down; a start was attempted '
                          f'{now - self.last_worker_start:.0f}s ago (limit one per {WORKER_START_INTERVAL}s)',
                          recovery='rate_limited')

        conn = self._connect('rescue', recover=False)
        started = None

        try:
            rescue = conn.rescue
            status = rescue.query_worker()

            if not status['running']:
                self.last_worker_start = now
                self.restart_expected = True
                self.events.emit('worker_start', async_api=bool(conn.caps[1] & 1))
                started = rescue.start_worker()
        except Exception as exc:
            error = classify(exc, 'rescue')

            if error.kind == 'uncertain':
                self.dirty['rescue'] = True

            raise OpError('worker_down', f'Worker recovery through Rescue failed: {error}',
                          recovery=error.to_dict())
        finally:
            conn.close()

        if self.wait_until(self._worker_answers, WORKER_START_WAIT):
            self.events.emit('worker_recovered', started=started is not None, pid=started)

            return {'started': started is not None, 'pid': started}

        raise OpError('worker_down', 'Worker did not answer op6 after recovery',
                      recovery={'started': started is not None, 'pid': started})

    def _worker_answers(self):
        try:
            self._connect('worker', recover=False).close()
        except OpError as error:
            if error.kind in ('worker_down', 'uncertain'):
                return False

            raise

        return True

    def verify_disappear(self, device, timeout=20):
        gone = self.wait_until(lambda: device not in self.present(), timeout)

        return {'outcome': 'applied' if gone else 'unknown', 'check': f'{device} left USB', 'observed': gone}

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
