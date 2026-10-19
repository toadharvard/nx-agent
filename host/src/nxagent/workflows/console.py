"""Worker sysmodule maintenance through the independent Rescue channel.

Install always requires an accepted known-good fallback on the SD card. Mutations are never replayed;
an uncertain step leaves an operation log to inspect before retrying.
"""
import hashlib
import json
import time
from pathlib import Path

from .. import paths
from ..common import OpError
from ..proto.rescue import GOOD, WORKER

ACTIONS = ('status', 'install', 'promote', 'rollback')


class OperationLog:
    def __init__(self, action, step):
        self.step = step
        self.log = {'operation': action, 'started_ns': time.time_ns(), 'steps': [], 'status': 'started'}
        self.dir = paths.operations_dir() / f'console-{action}-{self.log["started_ns"]}'
        self.dir.mkdir(parents=True)

    def record(self, stage, value=None):
        self.log['steps'].append({'stage': stage, 'value': value})
        (self.dir / 'manifest.json').write_text(json.dumps(self.log, indent=2, default=str) + '\n')
        self.step(stage)

    def finish(self, status):
        self.log['status'] = status
        self.record('end', status)

        return {'operation_log': str(self.dir / 'manifest.json'), **self.log}


def worker_status(daemon):
    status = daemon.run('rescue', 'query worker', (60,), lambda c: c.rescue.query_worker())
    status['launch'] = daemon.run('rescue', 'launch status', (65,), lambda c: c.rescue.launch_status())

    return status


def _verified(daemon, remote, sha256):
    return daemon.run('rescue', f'verify {remote}', (17,), lambda c: c.rescue.verified_image(remote, sha256))


def _probe_worker(daemon, seconds=30):
    if not daemon.wait_until(daemon._worker_answers, seconds):
        raise OpError('worker_down', 'Worker did not answer after start')

    return daemon.run('worker', 'probe app', (45,), lambda c: c.sidecar.app())


def _replace(daemon, log, target, content, create=False):
    log.record('worker_stop', daemon.run('rescue', 'stop worker', (61, 60), lambda c: c.rescue.stop_worker()))
    log.record('write', daemon.run('rescue', f'write {target}', (17, 32, 33, 34, 18),
                                   lambda c: c.rescue.put(target, content, create=create)))
    log.record('worker_start', daemon.run('rescue', 'start worker', (62, 64, 65, 66),
                                          lambda c: c.rescue.start_worker()))
    log.record('worker_probe', _probe_worker(daemon))


def console_update(daemon, action, sha256=None, image=None, fallback_sha256=None, device_tested=False,
                   step=lambda *a, **k: None):
    if action not in ACTIONS:
        raise OpError('invalid', f'action must be one of {ACTIONS}')

    if action == 'status':
        return worker_status(daemon)

    if not sha256:
        raise OpError('invalid', f'{action} requires sha256')

    log = OperationLog(action, step)

    try:
        if action == 'install':
            if not fallback_sha256 or not image:
                raise OpError('invalid', 'install requires image and fallback_sha256 (known-good on the console)')

            log.record('known_good_verified', len(_verified(daemon, GOOD, fallback_sha256)))
            content = Path(image).read_bytes()

            if hashlib.sha256(content).hexdigest() != sha256 or not content.startswith(b'PFS0'):
                raise OpError('invalid', 'Candidate image hash or format mismatch')

            _replace(daemon, log, WORKER, content)
        elif action == 'promote':
            if not device_tested:
                raise OpError('invalid', 'promote requires device_tested=true after on-device acceptance')

            log.record('worker_probe', _probe_worker(daemon))
            content = _verified(daemon, WORKER, sha256)
            exists = daemon.run('rescue', 'known-good exists', (18,), lambda c: c.rescue.good_exists())
            log.record('worker_stop', daemon.run('rescue', 'stop worker', (61, 60), lambda c: c.rescue.stop_worker()))

            try:
                log.record('write', daemon.run('rescue', 'write known-good', (17, 32, 33, 34, 18),
                                               lambda c: c.rescue.put(GOOD, content, create=not exists)))
            finally:
                log.record('worker_start', daemon.run('rescue', 'start worker', (62, 64, 65, 66),
                                                      lambda c: c.rescue.start_worker()))
                log.record('worker_probe_after', _probe_worker(daemon))
        else:
            _replace(daemon, log, WORKER, _verified(daemon, GOOD, sha256))
    except BaseException as exc:
        log.record('error', str(exc))
        log.finish('failed_or_uncertain_inspect_before_retry')
        raise

    return log.finish('completed')
