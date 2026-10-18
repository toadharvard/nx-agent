import asyncio
import functools
import hashlib
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fakes import make_daemon

from nxagent import client, mcp_server, paths
from nxagent.common import OpError
from nxagent.daemon import make_server
from nxagent.workflows import sd_install
from nxagent.workflows.hekate_ini import add_remote_entry

INI = '[config]\nautoboot=1\nautoboot_list=0\n\n[CFW]\npayload=bootloader/payloads/fusee.bin\n'


class HekateIni(unittest.TestCase):
    def test_appends_once_and_keeps_config(self):
        text, info = add_remote_entry(INI)

        self.assertEqual(info, {'changed': True, 'index': 2})
        self.assertTrue(text.startswith(INI))
        self.assertIn('[Remote USB Control]\npayload=bootloader/payloads/Remote-Hekate.bin\n', text)
        self.assertEqual(add_remote_entry(text), (text, {'changed': False, 'index': 2}))

    def test_preserves_crlf(self):
        text, _ = add_remote_entry(INI.replace('\n', '\r\n'))

        self.assertNotIn('\n[Remote', text.replace('\r\n', ''))
        self.assertTrue(text.endswith('Remote-Hekate.bin\r\n'))

    def test_refuses_ninth_entry(self):
        full = '[config]\n' + ''.join(f'[E{i}]\npayload=x\n' for i in range(8))

        with self.assertRaises(OpError):
            add_remote_entry(full)

    def test_refuses_existing_unreachable_entry(self):
        full = '[config]\n' + ''.join(f'[E{i}]\npayload=x\n' for i in range(8)) + '[Remote USB Control]\n'

        with self.assertRaises(OpError):
            add_remote_entry(full)


def make_bundle(root, files):
    manifest = {'version': 'test', 'files': {}}

    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest['files'][rel] = hashlib.sha256(data).hexdigest()

    (root / sd_install.MANIFEST).write_text(json.dumps(manifest))

    return root


def make_sd(root):
    (root / 'bootloader').mkdir(parents=True)
    (root / 'atmosphere' / 'contents').mkdir(parents=True)
    (root / 'bootloader' / 'hekate_ipl.ini').write_text(INI)

    return root


class SdInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sd = make_sd(self.tmp / 'sd')
        self.files = {'atmosphere/contents/420000000000D06C/exefs.nsp': b'PFS0rescue',
                      'atmosphere/contents/420000000000D06C/flags/boot2.flag': b'',
                      'atmosphere/contents/420000000000D06B/exefs.nsp': b'PFS0worker',
                      'bootloader/payloads/Remote-Hekate.bin': b'payload'}
        self.bundle = make_bundle(self.tmp / 'bundle', self.files)
        self.env = mock.patch.dict(os.environ, {'NXAGENT_STATE_DIR': str(self.tmp / 'state')})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_dry_run_changes_nothing(self):
        result = sd_install.install(self.sd, self.bundle)

        self.assertFalse(result['applied'])
        self.assertTrue(all(f['action'] == 'create' for f in result['files']))
        self.assertFalse((self.sd / 'bootloader/payloads/Remote-Hekate.bin').exists())
        self.assertEqual((self.sd / 'bootloader/hekate_ipl.ini').read_text(), INI)

    def test_apply_from_zip_with_backup(self):
        (self.sd / 'atmosphere/contents/420000000000D06B').mkdir(parents=True)
        zip_path = self.tmp / 'b.zip'

        with zipfile.ZipFile(zip_path, 'w') as archive:
            for path in self.bundle.rglob('*'):
                if path.is_file():
                    archive.write(path, path.relative_to(self.bundle))

        result = sd_install.install(self.sd, zip_path, apply=True)

        self.assertTrue(result['applied'])

        for rel, data in self.files.items():
            self.assertEqual((self.sd / rel).read_bytes(), data)

        self.assertIn('[Remote USB Control]', (self.sd / 'bootloader/hekate_ipl.ini').read_text())
        self.assertEqual((Path(result['backup']) / 'bootloader/hekate_ipl.ini').read_text(), INI)

    def test_refuses_foreign_sysmodule(self):
        foreign = self.sd / 'atmosphere/contents/420000000000D06B/exefs.nsp'
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(b'PFS0someone-else')

        with self.assertRaises(OpError):
            sd_install.install(self.sd, self.bundle)

        self.assertTrue(sd_install.install(self.sd, self.bundle, force=True)['forced_conflicts'])

    def test_rejects_non_switch_sd(self):
        with self.assertRaises(OpError):
            sd_install.install(self.tmp, self.bundle)

    def test_rejects_tampered_bundle(self):
        (self.bundle / 'bootloader/payloads/Remote-Hekate.bin').write_bytes(b'evil')

        with self.assertRaises(OpError):
            sd_install.install(self.sd, self.bundle)


class Paths(unittest.TestCase):
    def test_overrides(self):
        with mock.patch.dict(os.environ, {'NXAGENT_STATE_DIR': '/x/state', 'NXAGENT_RUNTIME_DIR': '/x/run',
                                          'NXAGENT_SOCKET': '/x/s.sock'}):
            self.assertEqual(paths.state_dir(), Path('/x/state'))
            self.assertEqual(paths.socket_path(), Path('/x/s.sock'))
            self.assertEqual(paths.journal_dir(), Path('/x/state/journal'))

    def test_long_socket_falls_back(self):
        with mock.patch.dict(os.environ, {'NXAGENT_SOCKET': '/x/' + 'a' * 120}):
            self.assertLess(len(str(paths.socket_path())), paths.SOCKET_LIMIT)


class Mcp(unittest.TestCase):
    def setUp(self):
        self.daemon, self.console, _ = make_daemon()
        self.socket = Path(tempfile.mkdtemp(prefix='nx', dir='/tmp')) / 'd.sock'
        self.server = make_server(self.daemon, self.socket)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.patches = [mock.patch.object(mcp_server, 'request', functools.partial(client.request,
                                                                                    socket_path=self.socket)),
                        mock.patch.object(mcp_server, 'ensure_daemon', lambda: None)]

        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

        self.server.shutdown()
        self.server.server_close()
        self.daemon.shutdown()

    def test_profiles(self):
        names = {p: {t.name for t in asyncio.run(mcp_server.build_server(p).list_tools())}
                 for p in mcp_server.PROFILES}

        self.assertIn('nx_screenshot', names['readonly'])
        self.assertNotIn('nx_press', names['readonly'])
        self.assertNotIn('nx_reboot', names['interactive'])
        self.assertIn('nx_sd_put_offline', names['full'])
        self.assertTrue(names['readonly'] < names['interactive'] < names['full'])
        self.assertFalse(any('power' in n for n in names['full']))

    def test_status_and_press_through_daemon(self):
        status = asyncio.run(mcp_server.nx_status())
        self.assertTrue(status['state'].startswith('hos'))

        asyncio.run(mcp_server.nx_press('A'))
        self.assertTrue(self.console.ops('worker', 41))

    def test_errors_carry_hints(self):
        self.console.fault('worker', 41, 'timeout')

        with self.assertRaises(Exception) as ctx:
            asyncio.run(mcp_server.nx_press('A'))

        self.assertIn('uncertain', str(ctx.exception))
        self.assertIn('Do NOT retry', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
