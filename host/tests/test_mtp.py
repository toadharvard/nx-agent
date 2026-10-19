import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from fakes import FakeMtpSession, make_daemon

from nxagent import mtp
from nxagent.common import HOS, MTP, OpError
from nxagent.mtp import MtpFiles, MtpMailbox, split_path

KIP = 'sdmc:/atmosphere/kips/loader.kip'

IOREG = '''+-o IOUSBHostInterface@0  <class IOUSBHostInterface>
    {
      "idProduct" = 8221
      "idVendor" = 1406
      "bInterfaceClass" = 6
      "UsbExclusiveOwner" = "pid 4242, {owner}"
    }
'''


class MtpCase(unittest.TestCase):
    def setUp(self):
        self.daemon, self.console, self.tmp = make_daemon()
        self.console.present = {MTP}
        self.daemon.monitor.poll()
        self.device = self.daemon.links.mtp_device
        self.device.add('atmosphere/kips/loader.kip', b'kip-v1')
        self.device.add('switch/app.nro', b'nro')
        self.journal = mock.patch('nxagent.paths.journal_dir', return_value=self.tmp / 'journal')
        self.journal.start()
        self.addCleanup(self.journal.stop)

    def local(self, data, name='src'):
        path = self.tmp / name
        path.write_bytes(data)

        return path

    def names(self, folder):
        return sorted(e['name'] for e in self.daemon.ls(folder))


class PathTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(split_path('sdmc:/a/b'), ['a', 'b'])
        self.assertEqual(split_path('/a//b/'), ['a', 'b'])
        self.assertEqual(split_path('sdmc:/'), [])

    def test_refused(self):
        for path in ('sdmc:/a/../b', 'sdmc:/Nintendo/save', 'sdmc:/emuMMC/RAW1', 'sdmc:/switch/prod.keys',
                     'sdmc:/a\tb'):
            with self.subTest(path=path), self.assertRaises(OpError) as ctx:
                split_path(path)

            self.assertEqual(ctx.exception.kind, 'invalid')


class RoutingTests(MtpCase):
    def test_ls_and_get_use_mtp(self):
        self.assertEqual(self.daemon.ls('sdmc:/atmosphere/kips'),
                         [{'name': 'loader.kip', 'directory': False, 'size': 6}])
        out = self.tmp / 'got' / 'loader.kip'
        result = self.daemon.get(KIP, out)
        self.assertEqual(out.read_bytes(), b'kip-v1')
        self.assertEqual(result['sha256'], hashlib.sha256(b'kip-v1').hexdigest())
        self.assertEqual(result['via'], 'mtp')

    def test_forbidden_path_never_reaches_device(self):
        with self.assertRaises(OpError):
            self.daemon.get('sdmc:/Nintendo/Contents/x', self.tmp / 'x')

        self.assertEqual(self.device.calls, [])

    def test_mutations_need_mtp_mode(self):
        self.console.present = {HOS}
        self.daemon.monitor.poll()

        for call in (lambda: self.daemon.mv(KIP, KIP + '.off'), lambda: self.daemon.rm(KIP),
                     lambda: self.daemon.mkdir('sdmc:/x')):
            with self.assertRaises(OpError) as ctx:
                call()

            self.assertEqual(ctx.exception.kind, 'invalid')

        self.assertEqual(self.device.calls, [])

    def test_absent_when_dbi_closed(self):
        self.console.present = set()

        with self.assertRaises(OpError) as ctx:
            self.daemon.links.mtp.ls('sdmc:/')

        self.assertEqual(ctx.exception.kind, 'absent')


class PutTests(MtpCase):
    def test_new_file_needs_create(self):
        with self.assertRaises(OpError) as ctx:
            self.daemon.put('sdmc:/switch/new.nro', self.local(b'new'))

        self.assertEqual(ctx.exception.kind, 'invalid')
        result = self.daemon.put('sdmc:/switch/new.nro', self.local(b'new'), create=True)
        self.assertFalse(result['replaced'])
        self.assertEqual(self.device.path_data('switch/new.nro'), b'new')

    def test_replace_swaps_verified_temp_and_journals_original(self):
        result = self.daemon.put(KIP, self.local(b'kip-v2'))
        self.assertTrue(result['replaced'])
        self.assertEqual(self.device.path_data('atmosphere/kips/loader.kip'), b'kip-v2')
        self.assertEqual(self.names('sdmc:/atmosphere/kips'), ['loader.kip'])
        journal = Path(result['journal'])
        self.assertEqual((journal / 'original').read_bytes(), b'kip-v1')
        self.assertEqual(json.loads((journal / 'manifest.json').read_text())['target'], KIP)
        ops = [c[0] for c in self.device.calls]
        self.assertLess(ops.index('P'), ops.index('R'))

    def test_replace_without_rename_support_falls_back(self):
        self.device.rename_ok = False
        result = self.daemon.put(KIP, self.local(b'kip-v3'))
        self.assertEqual(self.device.path_data('atmosphere/kips/loader.kip'), b'kip-v3')
        self.assertEqual(self.names('sdmc:/atmosphere/kips'), ['loader.kip'])
        self.assertEqual((Path(result['journal']) / 'original').read_bytes(), b'kip-v1')

    def test_failed_upload_keeps_original(self):
        self.device.fail['P'] = 1

        with self.assertRaises(OpError):
            self.daemon.put(KIP, self.local(b'kip-v4'))

        self.assertEqual(self.device.path_data('atmosphere/kips/loader.kip'), b'kip-v1')

    def test_directory_target_refused(self):
        with self.assertRaises(OpError):
            self.daemon.put('sdmc:/atmosphere/kips', self.local(b'x'))


class TreeTests(MtpCase):
    def test_rename_in_place(self):
        result = self.daemon.mv(KIP, 'sdmc:/atmosphere/kips/loader.kip.disabled')
        self.assertEqual(result['dest'], 'sdmc:/atmosphere/kips/loader.kip.disabled')
        self.assertEqual(self.names('sdmc:/atmosphere/kips'), ['loader.kip.disabled'])
        self.assertNotIn('V', [c[0] for c in self.device.calls])

    def test_move_into_directory(self):
        self.daemon.mkdir('sdmc:/backup')
        self.daemon.mv(KIP, 'sdmc:/backup')
        self.assertEqual(self.names('sdmc:/backup'), ['loader.kip'])
        self.assertEqual(self.names('sdmc:/atmosphere/kips'), [])

    def test_move_onto_existing_refused(self):
        with self.assertRaises(OpError) as ctx:
            self.daemon.mv('sdmc:/switch/app.nro', 'sdmc:/switch/app.nro')

        self.assertEqual(ctx.exception.kind, 'invalid')

    def test_rm_file_journaled(self):
        result = self.daemon.rm('sdmc:/switch/app.nro')
        self.assertEqual(self.names('sdmc:/switch'), [])
        self.assertEqual((Path(result['journal']) / 'original').read_bytes(), b'nro')

    def test_rm_directory_needs_recursive(self):
        with self.assertRaises(OpError):
            self.daemon.rm('sdmc:/atmosphere')

        self.daemon.rm('sdmc:/atmosphere', recursive=True)
        self.assertNotIn('atmosphere', self.names('sdmc:/'))

    def test_mkdir_parents(self):
        with self.assertRaises(OpError):
            self.daemon.mkdir('sdmc:/a/b/c')

        result = self.daemon.mkdir('sdmc:/a/b/c', parents=True)
        self.assertEqual(result['created'], ['sdmc:/a', 'sdmc:/a/b', 'sdmc:/a/b/c'])
        self.assertEqual(self.daemon.mkdir('sdmc:/a/b', parents=True)['created'], [])


class MailboxTests(MtpCase):
    def test_status_and_request_roundtrip(self):
        self.device.add('config/usb-worker-mailbox/status.json', json.dumps({'sequence': 7}).encode())
        box = MtpMailbox(self.daemon.links.mtp)
        self.assertEqual(box.read_status(), {'sequence': 7})
        self.assertEqual(box.submit(b'A' * 128), 0)
        self.assertEqual(box.submit(b'B' * 128), 0)
        self.assertEqual(self.device.path_data('config/usb-worker-mailbox/request.bin'), b'B' * 128)
        self.assertEqual([c[0] for c in self.device.calls].count('W'), 1)

    def test_unreadable_status_is_absent(self):
        box = MtpMailbox(self.daemon.links.mtp)

        with self.assertRaises(OpError) as ctx:
            box.read_status()

        self.assertEqual(ctx.exception.kind, 'absent')


class InterfaceOwnerTests(unittest.TestCase):
    def run_ioreg(self, owner):
        result = mock.Mock(stdout=IOREG.replace('{owner}', owner))

        return mock.patch('nxagent.mtp.subprocess.run', return_value=result)

    def test_other_owner_is_busy(self):
        with mock.patch('nxagent.mtp.sys.platform', 'darwin'), self.run_ioreg('node') as run:
            with self.assertRaises(OpError) as ctx:
                mtp.free_interface()

        self.assertEqual(ctx.exception.kind, 'busy')
        self.assertEqual(run.call_count, 1)

    def test_image_capture_is_released(self):
        with mock.patch('nxagent.mtp.sys.platform', 'darwin'), self.run_ioreg('ptpcamerad') as run, \
                mock.patch('nxagent.mtp.time.sleep'):
            self.assertEqual(mtp.free_interface(), (4242, 'ptpcamerad'))

        self.assertEqual(run.call_args_list[-1].args[0], ['pkill', '-9', '-x', 'ptpcamerad'])

    def test_other_platforms_skip(self):
        with mock.patch('nxagent.mtp.sys.platform', 'linux'), mock.patch('nxagent.mtp.subprocess.run') as run:
            self.assertIsNone(mtp.free_interface())

        run.assert_not_called()


class SessionFactoryTests(unittest.TestCase):
    def test_factory_is_used(self):
        files = MtpFiles(session=lambda: 'fake')
        self.assertEqual(files.session(), 'fake')
        self.assertTrue(issubclass(FakeMtpSession, mtp.Session))


if __name__ == '__main__':
    unittest.main()
