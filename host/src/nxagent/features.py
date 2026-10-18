"""Long-lived behaviours: control session (sleep lease + pad) and boot guard."""
import json
import os
import re
import struct
import threading
import time

from .common import HOS, MTP, OpError

LEASE_S = 120
RENEW_S = 45
DEFAULT_WAKE_ALARM_S = 120
FLAG = 'sdmc:/atmosphere/contents/{}/flags/boot2.flag'
DISABLED = '.sidecar-disabled'
GUARD_CONFIRM_AFTER_S = 60
GUARD72 = ('armed', 'boots', 'threshold', 'entries', 'last_revert_result', 'last_revert_count')


class Session:
    def __init__(self, daemon):
        self.d = daemon
        self.lock = threading.RLock()
        self.active = False
        self.pending_restore = False
        self.waiting = False
        self.until = self.next_renew = 0
        self.lease_gen = self.attach_gen = self.alarm_gen = None
        self.mode = 'rail'
        self.wake_alarm = 0
        self.renewals = 0
        self.last_error = None
        self.last_stop = None

    def start(self, minutes, mode='rail', wake_alarm=None):
        if not 1 <= int(minutes) <= 240:
            raise OpError('invalid', 'Session length must be 1..240 minutes')

        if mode not in ('rail', 'fullkey'):
            raise OpError('invalid', 'Controller mode must be rail or fullkey')

        if wake_alarm not in (None, 0) and not 60 <= int(wake_alarm) <= 3600:
            raise OpError('invalid', 'Wake alarm must be 0 or 60..3600 seconds')

        with self.lock:
            self.active, self.pending_restore, self.waiting = True, False, False
            self.until = self.d.clock.now() + int(minutes) * 60
            self.next_renew = 0
            self.lease_gen = self.attach_gen = self.alarm_gen = None
            self.mode = mode
            self.wake_alarm = DEFAULT_WAKE_ALARM_S if wake_alarm is None else int(wake_alarm)
            self.renewals, self.last_error = 0, None

        self.d.events.emit('session_start', minutes=int(minutes), mode=mode, wake_alarm=self.wake_alarm)
        self.tick()

        return self.status()

    def stop(self, reason='requested'):
        with self.lock:
            if self.active:
                self._stop(reason)

        return self.status()

    def status(self):
        now = self.d.clock.now()

        return {'active': self.active, 'mode': self.mode, 'wake_alarm_s': self.wake_alarm,
                'remaining_s': max(0, round(self.until - now)) if self.active else 0,
                'next_renew_in_s': max(0, round(self.next_renew - now)) if self.active else None,
                'renewals': self.renewals, 'waiting_for_device': self.waiting,
                'pending_restore': self.pending_restore, 'last_error': self.last_error,
                'last_stop': self.last_stop}

    def tick(self):
        with self.lock:
            state = self.d.monitor.state

            if not self.active:
                if self.pending_restore and state in ('hos', 'mtp'):
                    self._restore('deferred')

                return

            now = self.d.clock.now()

            if now >= self.until:
                self._stop('expired')

                return

            if state not in ('hos', 'mtp'):
                if not self.waiting:
                    self.d.events.emit('session_waiting', state=state)

                self.waiting = True

                return

            self.waiting = False
            generation = (state, self.d.monitor.generation[HOS if state == 'hos' else MTP])

            if self.lease_gen != generation or now >= self.next_renew:
                self._renew(generation, now)

            if self.attach_gen != generation:
                self._attach(generation)

            if self.alarm_gen != generation and state == 'hos':
                self._arm_alarm(generation)

    def _renew(self, generation, now):
        rearm = self.lease_gen != generation
        self.lease_gen, self.next_renew = generation, now + RENEW_S

        try:
            self.d.sleep_lease(LEASE_S)
        except OpError as error:
            self.last_error = error.to_dict()
            self.d.events.emit('lease_failed', **error.to_dict())

            return

        self.renewals += 1
        self.last_error = None
        self.d.events.emit('lease_renewed', seconds=LEASE_S, rearm=rearm)

    def _attach(self, generation):
        self.attach_gen = generation

        try:
            result = self.d.attach(self.mode)
            self.d.events.emit('session_attached', result=result)
        except OpError as error:
            self.last_error = error.to_dict()
            self.d.events.emit('session_attach_failed', **error.to_dict())

    def _arm_alarm(self, generation):
        self.alarm_gen = generation

        if not self.wake_alarm or not self.d.worker_has(8):
            return

        try:
            self.d.wake_alarm(self.wake_alarm)
        except OpError as error:
            self.d.events.emit('wake_alarm_failed', **error.to_dict())

    def _stop(self, reason):
        self.active = False
        self.last_stop = reason
        self.d.events.emit('session_stop', reason=reason)

        if self.d.monitor.state in ('hos', 'mtp'):
            self._restore(reason)
        else:
            self.pending_restore = True

    def _restore(self, reason):
        self.pending_restore = False
        results = {}
        steps = [('sleep_restored', lambda: self.d.sleep_lease(0)), ('detached', self.d.detach)]

        if self.wake_alarm and self.d.monitor.state == 'hos' and self.d.worker_has(8):
            steps.insert(1, ('wake_alarm_off', lambda: self.d.wake_alarm(0)))

        for name, action in steps:
            try:
                action()
                results[name] = True
            except OpError as error:
                results[name] = error.to_dict()

        self.d.events.emit('session_restored', reason=reason, results=results)


def normalize_tid(tid):
    if not re.fullmatch(r'[0-9a-fA-F]{16}', str(tid)):
        raise OpError('invalid', f'Invalid title ID: {tid}')

    return str(tid).upper()


def revert_entries(disable=(), enable=()):
    entries = []

    for tid in disable:
        base = FLAG.format(normalize_tid(tid))
        entries.append([base + DISABLED, base])

    for tid in enable:
        base = FLAG.format(normalize_tid(tid))
        entries.append([base, base + DISABLED])

    if not 1 <= len(entries) <= 8:
        raise OpError('invalid', 'Boot guard needs 1..8 flag changes')

    if len({entry[1] for entry in entries}) != len(entries):
        raise OpError('invalid', 'Duplicate title IDs in guard request')

    return entries


def guard_payload(entries):
    data = struct.pack('<I', len(entries))

    for source, target in entries:
        data += source.encode().ljust(200, b'\0') + target.encode().ljust(200, b'\0')

    return data


class Guard:
    """Boot guard: Rescue op70-72 when available, else a host-side emergency watcher."""

    def __init__(self, daemon):
        self.d = daemon
        self.path = daemon.state_dir / 'guard.json'
        self.lock = threading.RLock()
        self.confirm_after = int(daemon.config.get('guard_confirm_after_s', GUARD_CONFIRM_AFTER_S))
        self.next_check = 0

    def load(self):
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return None

    def save(self, journal):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(journal, indent=2))
        os.replace(temp, self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)

    def rescue_info(self):
        return self.d.run('rescue', 'rescue capabilities', (6, 9), lambda c: (c.version, c.caps))

    def device_supported(self):
        version, caps = self.rescue_info()

        return version >= 7 and bool(caps[5] & 1), version

    def arm(self, disable=(), enable=(), apply=False, threshold=2):
        entries = revert_entries(disable, enable)

        with self.lock:
            if (self.load() or {}).get('armed'):
                raise OpError('device_error', 'Boot guard already armed; confirm it first', name='EEXIST')

            supported, version = self.device_supported()
            journal = {'armed': True, 'entries': entries, 'created_ns': time.time_ns(),
                       'rescue_version': version, 'boots': 0, 'threshold': int(threshold)}

            if supported:
                self.d.run('rescue', 'guard arm', (70,), lambda c: c.cmd(70, guard_payload(entries)),
                           verify=lambda: self.verify_device(True, len(entries)))
                journal['mode'] = 'rescue'
                note = 'Rescue reverts on the 2nd unconfirmed boot; nxagentd confirms after a stable healthy boot'
            else:
                self.d.run('worker', 'guard baseline', (6,), lambda c: c.version)
                journal.update(mode='host_fallback', daemon_pid=os.getpid(),
                               restarts_seen=self.d.worker_boots)
                note = (f'Rescue v{version} has no boot guard. FALLBACK: nxagentd reverts through Worker op47 '
                        f'after {threshold} unconfirmed boots; works only while nxagentd runs and USB is connected')

            journal['note'] = note
            self.save(journal)

        self.d.events.emit('guard_armed', mode=journal['mode'], entries=entries)
        result = {'mode': journal['mode'], 'entries': entries, 'note': note}

        if apply:
            result['applied'] = [self.d.bootflag(tid, False) for tid in disable]
            result['applied'] += [self.d.bootflag(tid, True) for tid in enable]

        return result

    def covers(self, tid, enable):
        journal = self.load() or {}
        base = FLAG.format(tid)
        wanted = [base, base + DISABLED] if enable else [base + DISABLED, base]

        return bool(journal.get('armed')) and wanted in journal.get('entries', [])

    def verify_device(self, armed, entries=None):
        state = self.device_status()
        ok = state['armed'] == int(armed) and (entries is None or state['entries'] == entries)

        return {'outcome': 'applied' if ok else 'not_applied', 'op72': state}

    def device_status(self):
        payload = self.d.run('rescue', 'guard status', (72,), lambda c: c.cmd(72))

        return dict(zip(GUARD72, struct.unpack_from('<6I', payload)))

    def confirm(self, force=False):
        with self.lock:
            journal = self.load()

            if not force:
                health = self.d.health()

                if not health['healthy']:
                    raise OpError('device_error', 'Console is not healthy; not confirming', health=health)

            mode = journal['mode'] if journal else ('rescue' if self.device_supported()[0] else 'none')
            result = {'mode': mode}

            if mode == 'rescue':
                result['rescue'] = self._confirm_device()

            self.clear()

        self.d.events.emit('guard_confirmed', **result)

        return result

    def _confirm_device(self):
        try:
            self.d.run('rescue', 'guard confirm', (71,), lambda c: c.cmd(71),
                       verify=lambda: self.verify_device(False))
        except OpError as error:
            if error.details.get('code') == 2:
                return 'not_armed'

            raise

        return 'confirmed'

    def status(self):
        result = {'journal': self.load()}

        try:
            supported, version = self.device_supported()
            result['rescue_version'] = version
            result['rescue_guard'] = self.device_status() if supported else 'unsupported'
        except OpError as error:
            result['rescue_error'] = error.to_dict()

        return result

    def tick(self):
        with self.lock:
            journal = self.load()

            if not journal or not journal.get('armed') or self.d.monitor.state != 'hos':
                return

            if journal['mode'] == 'rescue':
                self._tick_rescue(journal)
            else:
                self._tick_fallback(journal)

    def _tick_rescue(self, journal):
        if self.d.clock.now() < self.next_check:
            return

        self.next_check = self.d.clock.now() + 5

        try:
            state = self.device_status()
        except OpError:
            return

        if not state['armed']:
            journal.update(armed=False, finished='reverted_by_rescue' if state['last_revert_count'] else 'cleared',
                           op72=state)
            self.save(journal)
            self.d.events.emit('guard_finished', op72=state)

            return

        if state['boots'] < 1:
            return

        health = self.d.health()
        now = self.d.clock.now()

        if not health['healthy']:
            journal.pop('healthy_since', None)
            self.save(journal)

            return

        since = journal.setdefault('healthy_since', now)
        self.save(journal)

        if now - since >= self.confirm_after:
            self.d.events.emit('guard_auto_confirm', boots=state['boots'], healthy_s=round(now - since))
            self.confirm(force=True)

    def _tick_fallback(self, journal):
        generation = self.d.monitor.generation[HOS]

        if journal.get('checked_gen') == generation:
            return

        try:
            self.d.run('worker', 'guard probe', (6,), lambda c: c.version)
        except OpError as error:
            self.d.events.emit('guard_probe_failed', **error.to_dict())

            return

        if journal.get('daemon_pid') != os.getpid():
            journal.update(daemon_pid=os.getpid(), restarts_seen=self.d.worker_boots)

        boots = self.d.worker_boots - journal['restarts_seen']
        journal.update(checked_gen=generation, restarts_seen=self.d.worker_boots)
        journal['boots'] += max(0, boots)
        self.save(journal)

        if journal['boots'] >= journal['threshold']:
            self._revert(journal)

    def _revert(self, journal):
        journal.update(armed=False, finished='reverting')
        self.save(journal)
        results = []

        for source, target in journal['entries']:
            tid = source.split('/')[3]
            directory = source.rsplit('/', 1)[0]

            try:
                names = {item['name'] for item in self.d.ls(directory)}

                if source.rsplit('/', 1)[1] not in names or target.rsplit('/', 1)[1] in names:
                    results.append({'tid': tid, 'skipped': True, 'files': sorted(names)})
                    continue

                results.append({'tid': tid, **self.d.bootflag(tid, target.endswith('boot2.flag'), guard_revert=True)})
            except OpError as error:
                results.append({'tid': tid, **error.to_dict()})

        journal.update(finished='reverted', revert=results)
        self.save(journal)
        self.d.events.emit('guard_fallback_reverted', results=results)
