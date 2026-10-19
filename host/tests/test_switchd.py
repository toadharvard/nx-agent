import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fakes import make_daemon, usb_timeout

from nxagent.common import HOS, MTP, NYX, OpError, classify
from nxagent.features import FLAG
from nxagent.links import build_mailbox_request
from nxagent.ops import HEKATE_INI, hekate_entry_index
from nxagent.daemon import AlreadyRunning, acquire_singleton

TID = '00FF0000636C6BFF'
FLAGS = f'sdmc:/atmosphere/contents/{TID}/flags'
INI = b"""[config]
autoboot=1
autoboot_list=0

[4mosphere]
payload=bootloader/payloads/fusee.bin
{--- Tools ---}
[Stock]
emummc_force_disable=1
[Remote USB Control]
payload=bootloader/payloads/Remote-Hekate.bin
"""


class Classification(unittest.TestCase):
    def test_audited_failure_strings(self):
        cases = [
            (RuntimeError('USB Debug Relay is not running or cable is disconnected'), 'absent'),
            (usb_timeout(), 'uncertain'),
            (RuntimeError('Sidecar command 6: console error 0x6f'), 'worker_down'),
            (RuntimeError('Sidecar command 6: console error 0x68'), 'worker_down'),
            (RuntimeError('Sidecar command 43: console error 0xe0ce'), 'device_error'),
            (RuntimeError('Sidecar command 41: console error 0x1c24ca'), 'device_error'),
            (RuntimeError('Another sidecar/debugger client owns USB'), 'busy'),
            (BlockingIOError(35, 'Resource temporarily unavailable'), 'busy'),
            (OSError(5, 'Input/Output Error'), 'uncertain'),
        ]

        for exc, kind in cases:
            self.assertEqual(classify(exc, 'worker').kind, kind, str(exc))

    def test_bridge_errno_on_rescue_interface_is_device_error(self):
        self.assertEqual(classify(RuntimeError('Sidecar command 60: console error 0x6f'), 'rescue').kind,
                         'device_error')


class RetryPolicy(unittest.TestCase):
    def test_read_only_retried_after_drain(self):
        daemon, console, _ = make_daemon()
        console.fault('worker', 45, 'timeout')
        self.assertTrue(daemon.app()['query_succeeded'])
        self.assertEqual(len(console.ops('worker', 45)), 2)
        self.assertEqual(console.drains['worker'], 1)

    def test_read_only_gives_up_after_three_retries(self):
        daemon, console, _ = make_daemon()
        console.fault('worker', 45, *['timeout'] * 4)

        with self.assertRaises(OpError) as ctx:
            daemon.app()

        self.assertEqual(ctx.exception.kind, 'uncertain')
        self.assertEqual(len(console.ops('worker', 45)), 4)

    def test_mutation_never_replayed_after_timeout(self):
        daemon, console, _ = make_daemon()
        console.fault('worker', 41, 'timeout')

        with self.assertRaises(OpError) as ctx:
            daemon.press('A', 100)

        self.assertEqual(ctx.exception.kind, 'uncertain')
        self.assertFalse(ctx.exception.details['replayed'])
        self.assertEqual(ctx.exception.details['verification']['outcome'], 'unknown')
        self.assertEqual(len(console.ops('worker', 41)), 1)
        self.assertEqual(console.drains['worker'], 1)
        self.assertTrue(console.ops('worker', 54), 'read-only verification must run')

    def test_uncertain_lease_verified_applied(self):
        daemon, console, _ = make_daemon()
        console.fault('worker', 50, 'timeout')
        result = daemon.sleep_lease(120)
        self.assertTrue(result['reply_lost'])
        self.assertEqual(result['verification']['outcome'], 'applied')
        self.assertEqual(len(console.ops('worker', 50)), 1)

    def test_lost_before_execution_reports_not_applied(self):
        daemon, console, _ = make_daemon()
        console.lease = 120
        console.fault('worker', 50, 'timeout_before')

        with self.assertRaises(OpError) as ctx:
            daemon.sleep_lease(0)

        self.assertEqual(ctx.exception.details['verification']['outcome'], 'not_applied')
        self.assertEqual(console.attempts.count(('worker', 50)), 1)

    def test_uncertain_put_verified_by_fingerprint(self):
        daemon, console, tmp = make_daemon()
        console.files['sdmc:/config/x.ini'] = b'old'
        local = tmp / 'new.ini'
        local.write_bytes(b'new content')
        console.fault('worker', 34, 'timeout')

        with mock.patch('nxagent.paths.journal_dir', return_value=tmp / 'journal'):
            result = daemon.put('sdmc:/config/x.ini', str(local))

        self.assertEqual(result['verification']['outcome'], 'applied')
        self.assertEqual(len(console.ops('worker', 32)), 1)
        self.assertEqual(len(console.ops('worker', 34)), 1)
        self.assertIsNotNone(result['journal'])

    def test_worker_down_mutation_not_resent(self):
        daemon, console, _ = make_daemon()
        console.fault('worker', 41, 111)

        with self.assertRaises(OpError) as ctx:
            daemon.press('B')

        self.assertEqual(ctx.exception.kind, 'worker_down')
        self.assertEqual(console.attempts.count(('worker', 41)), 1)

    def test_lock_held_elsewhere_is_busy(self):
        daemon, console, _ = make_daemon()
        console.locked.add('worker')

        with self.assertRaises(OpError) as ctx:
            daemon.app()

        self.assertEqual(ctx.exception.kind, 'busy')

    def test_busy_executor_cancels_queued_request(self):
        daemon, console, _ = make_daemon()
        daemon.queue_wait = 0.2
        release = threading.Event()
        blocker = threading.Thread(target=daemon.on_usb, args=(release.wait,))
        blocker.start()

        try:
            with self.assertRaises(OpError) as ctx:
                daemon.press('A')

            self.assertEqual(ctx.exception.kind, 'busy')
        finally:
            release.set()
            blocker.join()

        daemon.app()
        self.assertEqual(console.attempts.count(('worker', 41)), 0)

    def test_absent(self):
        daemon, console, _ = make_daemon()
        console.present = set()
        daemon.monitor.poll()

        with self.assertRaises(OpError) as ctx:
            daemon.app()

        self.assertEqual(ctx.exception.kind, 'absent')


class WorkerRecovery(unittest.TestCase):
    def test_exactly_one_start_within_30s(self):
        daemon, console, _ = make_daemon()
        console.worker_running = False
        console.start_brings_up = False

        for _ in range(2):
            with self.assertRaises(OpError) as ctx:
                daemon.app()

            self.assertEqual(ctx.exception.kind, 'worker_down')

        self.assertEqual(console.starts, 1)
        daemon.clock.advance(31)

        with self.assertRaises(OpError):
            daemon.app()

        self.assertEqual(console.starts, 2)

    def test_recovery_then_success_uses_async_launch(self):
        daemon, console, _ = make_daemon()
        console.worker_running = False
        self.assertTrue(daemon.app()['query_succeeded'])
        self.assertEqual(console.starts, 1)
        self.assertTrue(console.ops('rescue', 64))
        self.assertFalse(console.ops('rescue', 62))

    def test_sync_start_without_async_capability(self):
        daemon, console, _ = make_daemon()
        console.worker_running = False
        console.launch_ready = 0
        daemon.app()
        self.assertTrue(console.ops('rescue', 62))

    def test_running_worker_not_restarted(self):
        daemon, console, _ = make_daemon()
        console.fault('worker', 45, 111)
        daemon.app()
        self.assertEqual(console.starts, 0)


class Sessions(unittest.TestCase):
    def lease_values(self, console):
        return [struct.unpack('<I', call[2])[0] for call in console.ops('worker', 50)]

    def test_renews_and_rearms_after_reappear(self):
        daemon, console, _ = make_daemon()
        daemon.session.start(10)
        self.assertEqual(self.lease_values(console), [120])
        self.assertEqual(len(console.ops('worker', 40)), 1)
        self.assertEqual(console.wake_alarm, 120)

        daemon.clock.advance(46)
        daemon.session.tick()
        self.assertEqual(self.lease_values(console), [120, 120])
        self.assertEqual(len(console.ops('worker', 40)), 1)

        console.present = set()
        daemon.monitor.poll()
        daemon.clock.advance(60)
        daemon.session.tick()
        self.assertEqual(len(console.ops('worker', 50)), 2)
        self.assertTrue(daemon.session.status()['waiting_for_device'])

        console.present = {HOS}
        daemon.monitor.poll()
        daemon.session.tick()
        self.assertEqual(self.lease_values(console), [120, 120, 120])
        self.assertEqual(len(console.ops('worker', 40)), 2)

        daemon.session.stop()
        self.assertEqual(self.lease_values(console)[-1], 0)
        self.assertEqual(len(console.ops('worker', 42)), 1)
        self.assertEqual(console.wake_alarm, 0)

    def test_expiry_restores_sleep(self):
        daemon, console, _ = make_daemon()
        daemon.session.start(1)
        daemon.clock.advance(61)
        daemon.session.tick()
        self.assertFalse(daemon.session.active)
        self.assertEqual(self.lease_values(console)[-1], 0)

    def test_restore_deferred_until_device_returns(self):
        daemon, console, _ = make_daemon()
        daemon.session.start(1)
        console.present = set()
        daemon.monitor.poll()
        daemon.clock.advance(61)
        daemon.session.tick()
        self.assertTrue(daemon.session.pending_restore)
        console.present = {HOS}
        daemon.monitor.poll()
        daemon.session.tick()
        self.assertEqual(self.lease_values(console)[-1], 0)
        self.assertFalse(daemon.session.pending_restore)

    def test_bounds(self):
        daemon, _, _ = make_daemon()

        for minutes in (0, 241):
            with self.assertRaises(OpError):
                daemon.session.start(minutes)

    def test_session_over_mtp_mailbox(self):
        daemon, console, _ = make_daemon()
        console.present = {MTP}
        daemon.monitor.poll()
        daemon.session.start(5)
        self.assertEqual([call[1] for call in console.ops('mailbox', 50)], [50])
        self.assertEqual(len(console.ops('mailbox', 40)), 1)


class Singleton(unittest.TestCase):
    def test_second_instance_refuses(self):
        tmp = Path(tempfile.mkdtemp())
        first = acquire_singleton(tmp / 'd.lock', tmp / 'd.pid')

        try:
            with self.assertRaises(AlreadyRunning):
                acquire_singleton(tmp / 'd.lock', tmp / 'd.pid')
        finally:
            first.close()

        acquire_singleton(tmp / 'd.lock', tmp / 'd.pid').close()


class BootGuard(unittest.TestCase):
    def reboot(self, daemon, console):
        console.present = set()
        daemon.monitor.poll()
        console.commands = 0
        console.present = {HOS}
        daemon.monitor.poll()
        daemon.guard.tick()

    def test_rescue_payload(self):
        daemon, console, _ = make_daemon()
        result = daemon.guard.arm(disable=[TID.lower()])
        self.assertEqual(result['mode'], 'rescue')
        raw = console.guard['raw']
        self.assertEqual(len(raw), 404)
        self.assertEqual(raw[4:204].rstrip(b'\0').decode(), FLAG.format(TID) + '.sidecar-disabled')
        self.assertEqual(raw[204:404].rstrip(b'\0').decode(), FLAG.format(TID))

    def test_auto_confirm_after_stable_healthy_boot(self):
        daemon, console, _ = make_daemon()
        daemon.guard.arm(disable=[TID])
        console.guard['boots'] = 1
        daemon.guard.tick()
        self.assertFalse(console.ops('rescue', 71))
        daemon.clock.advance(61)
        daemon.guard.tick()
        self.assertEqual(len(console.ops('rescue', 71)), 1)
        self.assertIsNone(daemon.guard.load())

    def test_fallback_watcher_reverts_once(self):
        daemon, console, _ = make_daemon()
        console.rescue_version, console.rescue_features = 6, 0
        console.dirs[FLAGS] = ['boot2.flag']
        result = daemon.guard.arm(disable=[TID], apply=True)
        self.assertEqual(result['mode'], 'host_fallback')
        self.assertEqual(console.dirs[FLAGS], ['boot2.flag.sidecar-disabled'])
        enables = lambda: [c for c in console.ops('worker', 47) if c[2][0] == 1]  # noqa: E731

        self.reboot(daemon, console)
        self.assertEqual(enables(), [])
        self.reboot(daemon, console)
        self.assertEqual(len(enables()), 1)
        self.assertEqual(console.dirs[FLAGS], ['boot2.flag'])
        self.reboot(daemon, console)
        self.assertEqual(len(enables()), 1)
        self.assertEqual(daemon.guard.load()['finished'], 'reverted')

    def test_bootflag_requires_guard(self):
        daemon, console, _ = make_daemon()
        console.dirs[FLAGS] = ['boot2.flag']

        with self.assertRaises(OpError) as ctx:
            daemon.bootflag(TID, False)

        self.assertEqual(ctx.exception.kind, 'invalid')
        self.assertFalse(console.ops('worker', 47))


class RebootToConsole(unittest.TestCase):
    def test_entry_index(self):
        self.assertEqual(hekate_entry_index(INI.decode()), 3)
        self.assertIsNone(hekate_entry_index('[config]\n[A]\n'))

    def test_remote_waits_for_nyx(self):
        daemon, console, _ = make_daemon()
        console.files[HEKATE_INI] = INI
        console.on_reboot = [{HOS}, set(), set(), {NYX}]
        result = daemon.reboot_to('remote')
        self.assertEqual(console.ops('worker', 53)[0][2], b'R2C\x01\x03\x01')
        self.assertTrue(result['disappeared'])
        self.assertTrue(result['reached'])
        self.assertEqual(daemon.monitor.state, 'hekate_remote')

    def test_ums_waits_for_new_disk(self):
        daemon, console, _ = make_daemon()
        console.disks = {'disk2'}
        console.on_reboot = [set()]
        console.disk_timeline = [{'disk2'}, {'disk2'}, {'disk2', 'disk4'}]
        result = daemon.reboot_to('ums')
        self.assertEqual(console.ops('worker', 53)[0][2], b'R2C\x03\x00\x01')
        self.assertEqual(result['ums_disks'], ['disk4'])
        self.assertEqual(daemon.monitor.state, 'ums')

    def test_lost_reply_verified_by_disappearance(self):
        daemon, console, _ = make_daemon()
        console.on_reboot = [set()]
        console.fault('worker', 53, 'timeout')
        result = daemon.reboot_to('menu')
        self.assertTrue(result['reply_lost'])
        self.assertEqual(len(console.ops('worker', 53)), 1)

    def test_requires_feature(self):
        daemon, console, _ = make_daemon()
        console.worker_features = 63 & ~4

        with self.assertRaises(OpError) as ctx:
            daemon.reboot_to('menu')

        self.assertTrue(ctx.exception.details['unsupported'])

    def test_target_timeout(self):
        daemon, console, _ = make_daemon()
        console.files[HEKATE_INI] = INI
        console.on_reboot = [set()]

        with self.assertRaises(OpError) as ctx:
            daemon.reboot_to('remote', timeout=5)

        self.assertEqual(ctx.exception.kind, 'absent')


class RemoteHekate(unittest.TestCase):
    def nyx_daemon(self, version=2):
        daemon, console, tmp = make_daemon()
        console.present = {NYX}
        console.nyx_version = version
        daemon.monitor.poll()

        return daemon, console, tmp

    def test_v1_fallback(self):
        daemon, console, tmp = self.nyx_daemon(version=1)

        with self.assertRaises(OpError) as ctx:
            daemon.nyx_entries()

        self.assertTrue(ctx.exception.details['unsupported'])
        self.assertEqual(daemon.nyx_status()['version'], 1)
        result = daemon.screen(str(tmp / 'nyx.png'))
        self.assertTrue(Path(result['image']).exists())

    def test_entries_and_launch(self):
        daemon, console, _ = self.nyx_daemon()
        self.assertEqual(daemon.nyx_entries()[1], {'index': 2, 'name': 'Remote USB Control'})
        console.on_reboot = [set(), set(), {HOS}]
        result = daemon.nyx_action('launch')
        self.assertTrue(result['disappeared'])
        self.assertTrue(result['hos_appeared'])

    def test_screen_read_only_retry(self):
        daemon, console, tmp = self.nyx_daemon()
        console.fault('nyx', 3, 'timeout')
        daemon.nyx_screen(str(tmp / 's.png'))
        self.assertEqual(console.drains['nyx'], 1)


class Mailbox(unittest.TestCase):
    def mtp_daemon(self):
        daemon, console, tmp = make_daemon()
        console.present = {MTP}
        daemon.monitor.poll()

        return daemon, console

    def test_request_layout(self):
        request = build_mailbox_request('00000000000000ab', 6, 1000, 50, struct.pack('<I', 120))
        self.assertEqual(len(request), 128)
        self.assertEqual(struct.unpack_from('<8sQQQII', request), (b'SCMAIL01', 0xab, 6, 21000, 50, 4))

        with self.assertRaises(ValueError):
            build_mailbox_request('ab', 6, 0, 52, bytes(88))

    def test_press_once_via_mailbox(self):
        daemon, console = self.mtp_daemon()
        result = daemon.press('B', 200)
        self.assertEqual(result['sequence'], 6)
        self.assertEqual(len(daemon.links.mailbox.submits), 1)

    def test_claimed_mailbox_refuses(self):
        daemon, console = self.mtp_daemon()
        daemon.links.mailbox.status['phase'] = 'claimed'

        with self.assertRaises(OpError) as ctx:
            daemon.attach()

        self.assertEqual(ctx.exception.kind, 'uncertain')
        self.assertEqual(daemon.links.mailbox.submits, [])

    def test_unclaimed_request_not_resent(self):
        daemon, console = self.mtp_daemon()
        daemon.links.mailbox.ignore = True

        with self.assertRaises(OpError) as ctx:
            daemon.press('B')

        self.assertEqual(ctx.exception.details['verification']['outcome'], 'not_applied')
        self.assertEqual(len(daemon.links.mailbox.submits), 1)

    def test_sequence_limited_to_two_steps(self):
        daemon, console = self.mtp_daemon()

        with self.assertRaises(OpError):
            daemon.sequence([{'buttons': 'A', 'ms': 50}] * 3)

        daemon.sequence([{'buttons': 'A', 'ms': 50}] * 2)
        self.assertEqual(len(daemon.links.mailbox.submits), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class AppModules(unittest.TestCase):
    def test_old_worker_reply_has_no_module_fields(self):
        daemon, _, _ = make_daemon()
        app = daemon.app()
        self.assertTrue(app['query_succeeded'])
        self.assertNotIn('hbloader', app)

    def test_single_module_is_hbloader(self):
        daemon, console, _ = make_daemon()
        console.app_modules = (0, 1)
        app = daemon.app()
        self.assertEqual((app['modules'], app['hbloader']), (1, True))
        self.assertTrue(app['first_module_build_id'].startswith('a0ebd533'))

    def test_title_modules_are_not_hbloader(self):
        daemon, console, _ = make_daemon()
        console.app_modules = (0, 5)
        self.assertEqual((daemon.app()['modules'], daemon.app()['hbloader']), (5, False))

    def test_loader_error_is_reported_without_guess(self):
        daemon, console, _ = make_daemon()
        console.app_modules = (0x2f01, 0)
        app = daemon.app()
        self.assertEqual(app['modules_result'], '0x2f01')
        self.assertNotIn('hbloader', app)
