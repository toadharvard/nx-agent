import hashlib
import io
import struct
import unittest
from pathlib import Path
from unittest import mock

import usb.core

from fakes import make_daemon

from nxagent.common import OpError
from nxagent.proto.transport import MAGIC, STREAM_MAGIC, UsbSocket

PATH = 'sdmc:/switch/game.nsp'


class StreamDaemonTests(unittest.TestCase):
    def setUp(self):
        self.daemon, self.console, self.tmp = make_daemon()
        self.console.rescue_features = 5
        journal = mock.patch('nxagent.paths.journal_dir', return_value=self.tmp / 'journal')
        journal.start()
        self.addCleanup(journal.stop)

    def local(self, data, name='src'):
        path = self.tmp / name
        path.write_bytes(data)

        return path

    def ops(self, target='rescue'):
        return [c[1] for c in self.console.calls if c[0] == target]

    def test_put_new_file_streams(self):
        result = self.daemon.put(PATH, self.local(b'x' * 100000), create=True)
        self.assertEqual(self.console.files[PATH], b'x' * 100000)
        self.assertEqual(result['via'], 'bulk')
        self.assertIn(80, self.ops())
        self.assertNotIn(33, self.ops('worker'))

    def test_reply_timeouts_cover_console_work(self):
        self.console.files[PATH] = b'o' * 3_000_000
        self.daemon.put(PATH, self.local(b'n' * 2_000_000))
        timeouts = self.console.stream_timeouts
        self.assertIn((80, 30_000 + 5_000_000 // 10_000), timeouts)
        self.assertTrue(all(t >= 600_000 for op, t in timeouts if op == 81))

    def test_overwrite_keeps_old_copy(self):
        self.console.files[PATH] = b'old'
        result = self.daemon.put(PATH, self.local(b'new'))
        self.assertEqual(self.console.files[PATH], b'new')
        self.assertEqual((Path(result['journal']) / 'before.bin').read_bytes(), b'old')

    def test_get_streams_and_checks_hash(self):
        self.console.files[PATH] = b'payload' * 1000
        out = self.tmp / 'out.bin'
        result = self.daemon.get(PATH, out)
        self.assertEqual(out.read_bytes(), b'payload' * 1000)
        self.assertEqual(result['sha256'], hashlib.sha256(b'payload' * 1000).hexdigest())
        self.assertIn(81, self.ops())

    def test_corrupted_stream_leaves_file(self):
        self.console.files[PATH] = b'old'
        self.console.corrupt_stream = True

        with self.assertRaises(OpError):
            self.daemon.put(PATH, self.local(b'new content'))

        self.assertEqual(self.console.files[PATH], b'old')

    def test_framed_path_without_rescue_stream(self):
        self.console.rescue_features = 1
        self.daemon.put(PATH, self.local(b'small'), create=True)
        self.assertEqual(self.console.files[PATH], b'small')
        self.assertNotIn(80, self.ops())
        self.assertIn(33, self.ops('worker'))

    def test_config_can_turn_bulk_off(self):
        self.daemon.config = {'bulk_stream': False}
        self.daemon.put(PATH, self.local(b'small'), create=True)
        self.assertNotIn(80, self.ops())

    def test_large_upload_holds_sleep_lease(self):
        with mock.patch('nxagent.ops.LEASE_FROM', 10):
            self.daemon.put(PATH, self.local(b'y' * 64), create=True)

        leases = [struct.unpack('<I', c[2])[0] for c in self.console.calls if c[0] == 'worker' and c[1] == 50]
        self.assertEqual(leases, [600, 0])


class Endpoint:
    def __init__(self, reads=(), fail_after=None):
        self.reads, self.writes, self.fail_after = list(reads), [], fail_after

    def write(self, data, timeout=None):
        if self.fail_after is not None and len(self.writes) == self.fail_after:
            self.fail_after = None

            raise usb.core.USBTimeoutError('timeout', errno=60)

        self.writes.append(bytes(data))

        return len(data)

    def read(self, size, timeout=None):
        if not self.reads:
            raise usb.core.USBTimeoutError('timeout', errno=60)

        return self.reads.pop(0)


def socket_with(epin=None, epout=None):
    sock = UsbSocket.__new__(UsbSocket)
    sock.closed = sock.faulted = False
    sock.epin, sock.epout = epin or Endpoint(), epout or Endpoint()

    return sock


class TransportTests(unittest.TestCase):
    def test_failed_upload_pads_with_zeros(self):
        out = Endpoint(fail_after=1)
        sock = socket_with(epout=out)

        with mock.patch('nxagent.proto.transport.STREAM_CHUNK', 4), self.assertRaises(usb.core.USBTimeoutError):
            sock.write_stream(io.BytesIO(b'abcdefghij'), 10)

        self.assertEqual(b''.join(out.writes), b'abcd' + bytes(6))
        self.assertTrue(sock.faulted)

    def test_drain_skips_raw_stream_bytes(self):
        frame = struct.pack('<4I', MAGIC, 0, 0, 0) + bytes(4080)
        sock = socket_with(epin=Endpoint([b'raw' * 1000, frame]))
        self.assertEqual(sock.drain_pending_replies(seconds=2), 1)

    def test_stream_command_needs_marker(self):
        plain = struct.pack('<4I', MAGIC, 0, 0, 0) + bytes(4080)
        sock = socket_with(epin=Endpoint([plain]))

        with self.assertRaises(IOError):
            sock.stream_command(91, b'sdmc:/x')

        self.assertTrue(sock.faulted)
        marked = struct.pack('<4I', MAGIC, 0, 16, STREAM_MAGIC) + bytes(4080)
        self.assertEqual(len(socket_with(epin=Endpoint([marked])).stream_command(91, b'sdmc:/x')), 16)


if __name__ == '__main__':
    unittest.main()
