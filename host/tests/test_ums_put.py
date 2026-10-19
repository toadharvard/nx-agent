import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fakes import FakeClock

from nxagent.common import HOS, NYX
from nxagent.workflows import ums


class Monitor:
    def __init__(self):
        self.state = 'hekate_remote'

    def uptime(self, device):
        return 10


class Daemon:
    def __init__(self, root):
        self.clock = FakeClock()
        self.monitor = Monitor()
        self.actions = []
        self.root = root

    def wait_until(self, predicate, timeout, interval=0.5):
        deadline = self.clock.now() + timeout

        while self.clock.now() < deadline:
            if predicate():
                return True

            self.clock.sleep(interval)

        return False

    def present(self):
        return {HOS} if self.actions[-1:] == ['launch'] else {NYX}

    def nyx_action(self, action, wait=True, timeout=180):
        self.actions.append(action)
        self.monitor.state = 'ums'


class UmsRetryTests(unittest.TestCase):
    def test_ums_is_retried_once_when_it_closes_without_a_disk(self):
        root = Path(tempfile.mkdtemp())
        local = root.parent / f'{root.name}-src'
        local.write_bytes(b'rescue')
        daemon = Daemon(root)

        def find_sd(new, seconds=0):
            if daemon.actions.count('ums') < 2:
                daemon.monitor.state = 'hekate_remote' if daemon.clock.now() > 1006 else 'ums'

                return None, None

            return 'disk5', root

        with mock.patch.object(ums.disks, 'external_disks', return_value=set()), \
                mock.patch.object(ums.disks, 'find_sd', side_effect=find_sd), \
                mock.patch.object(ums.disks, 'eject'), mock.patch.object(ums.disks, 'strip_appledouble', return_value=0):
            result = ums.ums_put(daemon, [{'remote': 'atmosphere/contents/x/exefs.nsp', 'local': str(local)}])

        self.assertEqual(daemon.actions, ['ums', 'ums', 'launch'])
        self.assertEqual((root / 'atmosphere/contents/x/exefs.nsp').read_bytes(), b'rescue')
        self.assertEqual(len(result['copied']), 1)


if __name__ == '__main__':
    unittest.main()
