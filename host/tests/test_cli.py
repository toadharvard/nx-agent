import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path

from fakes import make_daemon

from nxagent import cli as switchctl
from nxagent.daemon import make_server


class CliExitCodes(unittest.TestCase):
    def setUp(self):
        self.daemon, self.console, _ = make_daemon()
        self.socket = Path(tempfile.mkdtemp(prefix='sw', dir='/tmp')) / 'd.sock'
        self.server = make_server(self.daemon, self.socket)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.daemon.shutdown()

    def cli(self, *argv, socket_path=None):
        out, err = io.StringIO(), io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = switchctl.main(list(argv), socket_path=socket_path or self.socket)
            except SystemExit as exit_:
                code = exit_.code

        self.last_err = err.getvalue()

        return code

    def test_ok(self):
        self.assertEqual(self.cli('status', '--cached'), 0)
        self.assertEqual(self.cli('press', 'A', '--ms', '50'), 0)
        self.assertEqual(self.cli('session', 'start', '--minutes', '5'), 0)
        self.assertEqual(self.cli('session', 'stop'), 0)

    def test_absent(self):
        self.console.present = set()
        self.daemon.monitor.poll()
        self.assertEqual(self.cli('app'), 2)

    def test_worker_down(self):
        self.console.worker_running = False
        self.console.start_brings_up = False
        self.assertEqual(self.cli('app'), 3)

    def test_uncertain(self):
        self.console.fault('worker', 41, 'timeout')
        self.assertEqual(self.cli('press', 'A'), 4)

    def test_device_error(self):
        self.console.screenshot_error = 0xe0ce
        self.assertEqual(self.cli('screen', '/tmp/never-written.jpg'), 5)

    def test_busy(self):
        self.console.locked.add('worker')
        self.assertEqual(self.cli('app'), 6)

    def test_usage_errors(self):
        self.assertEqual(self.cli('press', 'NOPE'), 64)
        self.assertEqual(self.cli('session', 'start'), 64)

    def test_daemon_not_running(self):
        self.assertEqual(self.cli('status', socket_path=Path('/tmp/no-such-nxagentd.sock')), 1)
        self.assertIn('nxagentd --background', self.last_err)


if __name__ == '__main__':
    unittest.main(verbosity=2)
