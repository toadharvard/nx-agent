import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nxagent.common import OpError
from nxagent.workflows.console import console_update


class Daemon:
    """Rescue refuses writes (EBUSY) while the Worker runs, like the console."""

    def __init__(self):
        self.running = True
        self.names = []

    def wait_until(self, predicate, timeout):
        return True

    def _worker_answers(self):
        return self.running

    def run(self, target, name, ops, body, verify=None):
        self.names.append(name)

        if name.startswith('verify'):
            return b'PFS0image'

        if name == 'known-good exists':
            return True

        if name == 'stop worker':
            self.running = False
        elif name == 'start worker':
            self.running = True
        elif name == 'write known-good' and self.running:
            raise OpError('device_error', 'rescue op32: console error EBUSY')

        return {}


class PromoteTests(unittest.TestCase):
    def setUp(self):
        ops = mock.patch('nxagent.paths.operations_dir', return_value=Path(tempfile.mkdtemp()))
        ops.start()
        self.addCleanup(ops.stop)

    def test_promote_stops_worker_for_the_write_and_restarts_it(self):
        daemon = Daemon()
        console_update(daemon, 'promote', sha256='ab' * 32, device_tested=True)
        self.assertEqual(daemon.names[-4:], ['stop worker', 'write known-good', 'start worker', 'probe app'])
        self.assertTrue(daemon.running)

    def test_failed_write_still_restarts_worker(self):
        daemon = Daemon()
        original = daemon.run

        def run(target, name, ops, body, verify=None):
            if name == 'write known-good':
                daemon.names.append(name)

                raise OpError('uncertain', 'reply lost')

            return original(target, name, ops, body, verify)

        daemon.run = run

        with self.assertRaises(OpError):
            console_update(daemon, 'promote', sha256='ab' * 32, device_tested=True)

        self.assertTrue(daemon.running)
        self.assertIn('start worker', daemon.names)


if __name__ == '__main__':
    unittest.main()
