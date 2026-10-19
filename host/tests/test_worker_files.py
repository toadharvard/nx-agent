import unittest
from pathlib import Path
from unittest import mock

from fakes import make_daemon

from nxagent.common import OpError

KIPS = 'sdmc:/atmosphere/kips'
KIP = KIPS + '/loader.kip'


class WorkerTreeTests(unittest.TestCase):
    def setUp(self):
        self.daemon, self.console, self.tmp = make_daemon()
        self.console.worker_features |= 256
        self.console.dirs.update({'sdmc:/': ['atmosphere', 'switch'], 'sdmc:/atmosphere': ['kips'],
                                  KIPS: ['loader.kip'], 'sdmc:/switch': ['app.nro']})
        self.console.files.update({KIP: b'kip', 'sdmc:/switch/app.nro': b'nro'})
        journal = mock.patch('nxagent.paths.journal_dir', return_value=self.tmp / 'journal')
        journal.start()
        self.addCleanup(journal.stop)

    def names(self, path):
        return sorted(e['name'] for e in self.daemon.ls(path))

    def test_rename_kip_in_hos(self):
        result = self.daemon.mv(KIP, KIP + '.disabled')
        self.assertEqual(result['via'], 'worker')
        self.assertEqual(self.names(KIPS), ['loader.kip.disabled'])
        self.assertEqual(self.console.files[KIP + '.disabled'], b'kip')
        self.assertIn(36, [c[1] for c in self.console.calls if c[0] == 'worker'])

    def test_move_into_directory_and_refuse_overwrite(self):
        self.daemon.mv('sdmc:/switch/app.nro', KIPS)
        self.assertEqual(self.names(KIPS), ['app.nro', 'loader.kip'])

        with self.assertRaises(OpError) as ctx:
            self.daemon.mv(KIPS + '/app.nro', KIP)

        self.assertEqual(ctx.exception.kind, 'invalid')

    def test_rm_file_is_journaled(self):
        result = self.daemon.rm(KIP)
        self.assertEqual(self.names(KIPS), [])
        self.assertEqual((Path(result['journal']) / 'original').read_bytes(), b'kip')

    def test_rm_tree_needs_recursive_and_backs_up_every_file(self):
        with self.assertRaises(OpError):
            self.daemon.rm('sdmc:/atmosphere')

        result = self.daemon.rm('sdmc:/atmosphere', recursive=True)
        self.assertEqual(self.names('sdmc:/'), ['switch'])
        self.assertEqual((Path(result['journal']) / 'tree' / 'kips' / 'loader.kip').read_bytes(), b'kip')

    def test_mkdir_parents(self):
        with self.assertRaises(OpError):
            self.daemon.mkdir('sdmc:/a/b')

        self.assertEqual(self.daemon.mkdir('sdmc:/a/b', parents=True)['created'], ['sdmc:/a', 'sdmc:/a/b'])
        self.assertEqual(self.names('sdmc:/a'), ['b'])

    def test_forbidden_paths_rejected_on_host(self):
        for path in ('sdmc:/Nintendo/x', 'sdmc:/switch/prod.keys', 'sdmc:/a/../b'):
            with self.subTest(path=path), self.assertRaises(OpError) as ctx:
                self.daemon.rm(path)

            self.assertEqual(ctx.exception.kind, 'invalid')


if __name__ == '__main__':
    unittest.main()
