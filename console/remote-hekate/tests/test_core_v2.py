# SPDX-License-Identifier: GPL-2.0-only
"""Host tests for Remote Hekate v2 core: ops 6-12, watchdog, deferred actions, entries."""
import ctypes as c
import os
import random
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
lib = c.CDLL(os.environ.get('REMOTE_CORE_LIB') or str(HERE / ('core.dylib' if sys.platform == 'darwin' else 'core.so')))
U32 = c.c_uint32
ENTRIES_FN = c.CFUNCTYPE(U32, c.c_void_p, c.c_void_p, U32, c.POINTER(U32))


class Sec(c.Structure):
    _fields_ = [('type', U32), ('name', c.c_char_p)]


lib.remote_core_request.argtypes = [c.c_void_p, U32, c.c_void_p, U32, c.c_void_p]
lib.remote_core_init.argtypes = [U32, U32, U32]
lib.remote_core_set_entries.argtypes = [c.c_void_p, c.c_void_p]
lib.remote_core_set_nested.argtypes = [c.c_int]
lib.remote_core_kick.argtypes = [U32]
lib.remote_core_parse_watchdog.argtypes = [c.c_char_p]
lib.remote_core_parse_watchdog.restype = U32
lib.remote_core_reply_done.argtypes = [U32]
lib.remote_core_reply_failed.argtypes = [U32]
lib.remote_core_take_action.argtypes = [U32, c.POINTER(U32)]
lib.remote_core_take_action.restype = c.c_int
lib.remote_core_format_entries.argtypes = [c.POINTER(Sec), U32, c.c_char_p, U32, c.POINTER(U32)]
lib.remote_core_format_entries.restype = U32
lib.remote_core_check_hekate.argtypes = [c.c_char_p, U32]
lib.remote_core_check_hekate.restype = c.c_int

MAGIC = 0x5258594E
SIZE = 1280 * 720 * 4
NONE, LAUNCH, UMS, REBOOT, POWEROFF = range(5)
CAP_OFW, CAP_NORMAL = 1, 2
CHOICE, CAPTION, NEWLINE, COMMENT = 3, 5, 0xFE, 0xFF
frame = c.create_string_buffer(b'abcd' * (SIZE // 4))


def request(op, *args, now=100, seq=7, length=64, magic=MAGIC):
    q = struct.pack('<16I', magic, seq, op, *args, *([0] * (13 - len(args))))
    out = c.create_string_buffer(4096)
    lib.remote_core_request(q, length, out, now, frame)
    h = struct.unpack_from('<8I', out.raw)
    return h, out.raw[32:32 + h[3]]


def take(now):
    arg = U32(0xDEAD)
    kind = lib.remote_core_take_action(now, c.byref(arg))
    return kind, arg.value


def status(now):
    h, b = request(12, now=now)
    return struct.unpack('<4I', b)


def secs(*items):
    arr = (Sec * max(1, len(items)))()
    for i, (t, n) in enumerate(items):
        arr[i].type = t
        arr[i].name = n.encode() if n is not None else None
    return arr, len(items)


def fmt(*items, cap=4064):
    arr, n = secs(*items)
    out = c.create_string_buffer(cap + 16)
    ln = U32(0)
    err = lib.remote_core_format_entries(arr, n, out, cap, c.byref(ln))
    return err, out.raw[:ln.value]


DEVICE_INI = [(CHOICE, 'config'), (CHOICE, '4mosphere'), (CHOICE, 'Stock (semi-stock)')]


class Base(unittest.TestCase):
    def setUp(self):
        lib.remote_core_init(1000, 90, CAP_OFW | CAP_NORMAL)
        lib.remote_core_set_entries(None, None)


class Watchdog(Base):
    def test_expiry_launches_once_and_rearms(self):
        self.assertEqual(take(1000 + 89999)[0], NONE)
        self.assertEqual(take(1000 + 90000), (LAUNCH, 0))
        self.assertEqual(take(1000 + 90001)[0], NONE)
        self.assertEqual(take(1000 + 90000 + 89999)[0], NONE)
        self.assertEqual(take(1000 + 180000)[0], LAUNCH)

    def test_valid_request_resets_countdown(self):
        self.assertEqual(request(1, now=50000)[0][2], 0)
        self.assertEqual(take(91000)[0], NONE)
        self.assertEqual(take(50000 + 89999)[0], NONE)
        self.assertEqual(take(50000 + 90000)[0], LAUNCH)

    def test_request_with_bad_args_but_known_op_still_resets(self):
        self.assertEqual(request(4, 5000, 0, 10, now=60000)[0][2], 2)
        self.assertEqual(take(91000)[0], NONE)

    def test_malformed_requests_do_not_reset(self):
        request(1, now=40000, magic=0x12345678)
        request(1, now=40000, length=63)
        request(0, now=40000)
        request(13, now=40000)
        request(0xFFFFFFFF, now=40000)
        self.assertEqual(take(91000)[0], LAUNCH)
        self.assertEqual(status(91001)[3], 1)

    def test_disabled_by_config(self):
        lib.remote_core_init(1000, 0, 0)
        for t in (91000, 10**6, 2**31, 2**32 - 1):
            self.assertEqual(take(t)[0], NONE)
        self.assertEqual(status(5000), (2, 0, 0, 1))

    def test_op11_disable_reenable_and_bounds(self):
        self.assertEqual(request(11, 0, now=2000)[0][2], 0)
        self.assertEqual(take(10**6)[0], NONE)
        for bad in (1, 29, 3601, 0xFFFFFFFF):
            self.assertEqual(request(11, bad, now=3000)[0][2], 2)
        self.assertEqual(take(10**6)[0], NONE)
        self.assertEqual(request(11, 30, 1, now=3000)[0][2], 2)
        self.assertEqual(request(11, 30, now=4000)[0][2], 0)
        self.assertEqual(take(4000 + 29999)[0], NONE)
        self.assertEqual(take(4000 + 30000)[0], LAUNCH)
        self.assertEqual(request(11, 3600, now=5000)[0][2], 0)
        self.assertEqual(status(5000)[1:3], (3600, 1))

    def test_kick_restarts_without_counting(self):
        lib.remote_core_kick(80000)
        self.assertEqual(take(91000)[0], NONE)
        self.assertEqual(take(170000)[0], LAUNCH)
        self.assertEqual(status(170001)[3], 1)

    def test_clock_wrap(self):
        lib.remote_core_init(0xFFFFF000, 30, 0)
        self.assertEqual(take(0xFFFFF000 + 29999 & 0xFFFFFFFF)[0], NONE)
        self.assertEqual(take((0xFFFFF000 + 30000) & 0xFFFFFFFF)[0], LAUNCH)

    def test_status_reports_seconds_left_and_count(self):
        self.assertEqual(status(1000), (2, 90, 1, 1))
        request(1, now=1000)
        self.assertEqual(status(1500), (2, 90, 1, 3))
        lib.remote_core_kick(1000)
        self.assertEqual(struct.unpack('<4I', request(12, now=1000)[1])[1], 90)

    def test_parse_watchdog(self):
        cases = {b'90': 90, b'0': 0, b'00': 0, b'5': 30, b'30': 30, b'3600': 3600, b'99999': 3600,
                 b'abc': 90, b'': 90, b'-1': 90, b' 90': 90, b'90s': 90, b'12345678': 90, None: 90}
        for raw, want in cases.items():
            self.assertEqual(lib.remote_core_parse_watchdog(raw), want, raw)


class Ops(Base):
    def test_unknown_and_arg_validation(self):
        for op in (0, 13, 14, 0xFFFFFFFF):
            self.assertEqual(request(op)[0][2], 1)
        self.assertEqual(request(7, 1)[0][2], 2)
        self.assertEqual(request(7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)[0][2], 2)
        self.assertEqual(request(8, 0, 5)[0][2], 2)
        self.assertEqual(request(9, 2)[0][2], 2)
        self.assertEqual(request(9, 0, 1)[0][2], 2)
        self.assertEqual(request(10, 0xFFFFFFFF)[0][2], 2)
        lib.remote_core_reply_done(200)
        self.assertEqual(take(5000)[0], NONE)

    def test_reply_header_echo(self):
        for op in range(1, 13):
            lib.remote_core_init(1000, 90, 3)
            h, _ = request(op, seq=0xABCDEF01, now=1000)
            self.assertEqual((h[0], h[1], h[4]), (MAGIC, 0xABCDEF01, op))

    def test_reboot_caps(self):
        lib.remote_core_init(1000, 90, CAP_NORMAL)
        self.assertEqual(request(9, 0)[0][2], 6)
        self.assertEqual(request(9, 1)[0][2], 0)
        lib.remote_core_init(1000, 90, CAP_OFW)
        self.assertEqual(request(9, 1)[0][2], 6)
        self.assertEqual(request(9, 0)[0][2], 0)

    def test_busy_while_pending_and_read_ops_still_work(self):
        self.assertEqual(request(7, now=2000)[0][2], 0)
        for op in (7, 8, 9, 10):
            self.assertEqual(request(op, now=2001)[0][2], 5)
        self.assertEqual(request(1, now=2002)[0][2], 0)
        self.assertEqual(status(2003)[0], 2)

    def test_nested_context_defers_sd_and_side_effects(self):
        lib.remote_core_set_nested(1)
        for op in (6, 7, 8, 9, 10):
            self.assertEqual(request(op, now=2000)[0][2], 5)
        for op in (1, 2, 5, 12):
            self.assertEqual(request(op, now=2000)[0][2], 0)
        self.assertEqual(request(11, 60, now=2000)[0][2], 0)
        lib.remote_core_reply_done(2000)
        self.assertEqual(take(5000)[0], NONE)
        lib.remote_core_set_nested(0)
        self.assertEqual(request(8, now=6000)[0][2], 0)

    def test_ops_1_to_5_unchanged(self):
        h, b = request(1)
        self.assertEqual(struct.unpack('<6I', b), (1, 1280, 720, 4, SIZE, 500))
        g = struct.unpack('<I', request(2)[1])[0]
        self.assertEqual(request(3, g, 4, 8)[1], b'abcdabcd')
        self.assertEqual(request(4, 1, 2, 3)[0][2], 0)
        self.assertEqual(request(5)[0][2], 0)


class Deferred(Base):
    def test_action_runs_only_after_reply_plus_delay(self):
        for op, kind, arg in ((7, LAUNCH, 0), (8, UMS, 0), (9, REBOOT, 0), (9, REBOOT, 1), (10, POWEROFF, 0)):
            lib.remote_core_init(1000, 90, 3)
            args = (arg,) if op == 9 else ()
            self.assertEqual(request(op, *args, now=2000)[0][2], 0)
            self.assertEqual(take(2000)[0], NONE)
            self.assertEqual(take(3999)[0], NONE)
            lib.remote_core_reply_done(3000)
            self.assertEqual(take(3199)[0], NONE)
            self.assertEqual(take(3200), (kind, arg))
            self.assertEqual(take(3201)[0], NONE)

    def test_host_vanished_reply_timeout(self):
        self.assertEqual(request(7, now=2000)[0][2], 0)
        self.assertEqual(take(3999)[0], NONE)
        self.assertEqual(take(4000), (LAUNCH, 0))

    def test_reply_failed_counts_as_delivered(self):
        request(8, now=2000)
        lib.remote_core_reply_failed(2100)
        self.assertEqual(take(2299)[0], NONE)
        self.assertEqual(take(2300)[0], UMS)

    def test_rejected_request_never_arms(self):
        request(7, 1, now=2000)
        lib.remote_core_reply_done(2000)
        self.assertEqual(take(10000)[0], NONE)

    def test_later_reply_does_not_promote_unanswered_action(self):
        request(7, now=2000)
        request(12, now=2100)
        lib.remote_core_reply_done(2100)
        self.assertEqual(take(2400)[0], NONE)
        self.assertEqual(take(4000)[0], LAUNCH)

    def test_action_rearms_watchdog(self):
        request(8, now=2000)
        lib.remote_core_reply_done(2000)
        self.assertEqual(take(2200)[0], UMS)
        self.assertEqual(take(2200 + 89999)[0], NONE)
        self.assertEqual(take(2200 + 90000)[0], LAUNCH)


class Entries(Base):
    def test_device_ini_numbering(self):
        self.assertEqual(fmt(*DEVICE_INI), (0, b'1\t4mosphere\n2\tStock (semi-stock)\n'))

    def test_with_remote_entry_appended(self):
        err, txt = fmt(*DEVICE_INI, (NEWLINE, None), (CHOICE, 'Remote USB Control'))
        self.assertEqual(txt.splitlines()[-1], b'3\tRemote USB Control')

    def test_non_choice_sections_skipped(self):
        err, txt = fmt((COMMENT, 'c'), (CHOICE, 'config'), (CAPTION, 'Cap'), (NEWLINE, None),
                       (CHOICE, 'A'), (COMMENT, 'x'), (CHOICE, 'B'))
        self.assertEqual(txt, b'1\tA\n2\tB\n')

    def test_sections_before_config_consume_indices_but_are_not_listed(self):
        self.assertEqual(fmt((CHOICE, 'Early'), (CHOICE, 'config'), (CHOICE, 'A'))[1], b'2\tA\n')

    def test_second_config_is_a_regular_entry(self):
        self.assertEqual(fmt((CHOICE, 'config'), (CHOICE, 'A'), (CHOICE, 'config'))[1], b'1\tA\n2\tconfig\n')

    def test_no_config_lists_nothing(self):
        self.assertEqual(fmt((CHOICE, 'A'), (CHOICE, 'B')), (0, b''))
        self.assertEqual(fmt(), (0, b''))

    def test_control_chars_sanitized(self):
        self.assertEqual(fmt((CHOICE, 'config'), (CHOICE, 'a\tb\x7fc\x01'))[1], b'1\ta?b?c?\n')

    def test_overflow_is_an_error_not_truncation(self):
        name = 'x' * 100
        err, txt = fmt((CHOICE, 'config'), *[(CHOICE, name)] * 40)
        self.assertEqual(err, 7)
        self.assertEqual(txt, b'')
        err, txt = fmt((CHOICE, 'config'), (CHOICE, 'abc'), cap=6)
        self.assertEqual((err, txt), (0, b'1\tabc\n'))
        self.assertEqual(fmt((CHOICE, 'config'), (CHOICE, 'abc'), cap=5)[0], 7)

    def test_op6_through_provider(self):
        arr, n = secs(*DEVICE_INI)

        @ENTRIES_FN
        def provider(ctx, out, cap, ln):
            return lib.remote_core_format_entries(arr, n, c.cast(out, c.c_char_p), cap, ln)

        lib.remote_core_set_entries(provider, None)
        h, b = request(6)
        self.assertEqual((h[2], b), (0, b'1\t4mosphere\n2\tStock (semi-stock)\n'))
        lib.remote_core_set_entries(None, None)

    def test_op6_provider_errors(self):
        self.assertEqual(request(6)[0][2], 6)

        @ENTRIES_FN
        def failing(ctx, out, cap, ln):
            return 6

        @ENTRIES_FN
        def liar(ctx, out, cap, ln):
            ln[0] = cap + 1
            return 0

        for fn, err in ((failing, 6), (liar, 7)):
            lib.remote_core_set_entries(fn, None)
            h, b = request(6)
            self.assertEqual((h[2], h[3]), (err, 0))
        lib.remote_core_set_entries(None, None)


class Image(unittest.TestCase):
    def test_real_images(self):
        release = HERE.parent.parent / 'release' / 'payload.bin'
        ours = HERE.parent / 'output' / 'hekate.bin'
        nyx = HERE.parent / 'output' / 'nyx.bin'
        for p, want in ((release, 1), (ours, 1), (nyx, 0)):
            if p.exists():
                b = p.read_bytes()
                self.assertEqual(lib.remote_core_check_hekate(b, len(b)), want, p)

    def test_synthetic(self):
        img = bytearray(0x200)
        struct.pack_into('<I', img, 0x118, 0x43544349)
        self.assertEqual(lib.remote_core_check_hekate(bytes(img), len(img)), 1)
        self.assertEqual(lib.remote_core_check_hekate(bytes(img), 0x11F), 0)
        self.assertEqual(lib.remote_core_check_hekate(bytes(img) + bytes(0x30000), 0x30001), 0)
        self.assertEqual(lib.remote_core_check_hekate(None, 0x200), 0)
        img[0x118] = 0
        self.assertEqual(lib.remote_core_check_hekate(bytes(img), len(img)), 0)


class Fuzz(Base):
    def test_10000_malformed_requests_valid_magic(self):
        rng = random.Random(20261015)
        allowed_err = {0, 1, 2, 3, 4, 5, 6, 7}
        now = 1000
        for i in range(10000):
            now += rng.randrange(0, 50)
            op = rng.choice([rng.randrange(0, 16), rng.randrange(6, 13), rng.getrandbits(32)])
            args = [rng.choice([0, 0, 0, 1, 2, 30, 3600, rng.getrandbits(32)]) for _ in range(13)]
            q = struct.pack('<16I', MAGIC, i, op, *args)
            length = 64 if rng.random() < 0.95 else rng.randrange(0, 64)
            out = c.create_string_buffer(b'X' * 4112)
            lib.remote_core_request(q, length, out, now, frame)
            self.assertEqual(out.raw[4096:4112], b'X' * 16)
            h = struct.unpack_from('<8I', out.raw)
            self.assertEqual(h[0], MAGIC)
            self.assertIn(h[2], allowed_err)
            self.assertLessEqual(h[3], 4064)
            if length != 64:
                self.assertEqual(h[2], 2)
            armed = length == 64 and h[2] == 0 and op in (7, 8, 9, 10)
            lib.remote_core_reply_done(now)
            kind, _ = take(now + 200)
            if armed:
                self.assertEqual(kind, {7: LAUNCH, 8: UMS, 9: REBOOT, 10: POWEROFF}[op])
            else:
                self.assertIn(kind, (NONE, LAUNCH))


if __name__ == '__main__':
    unittest.main(verbosity=2)
