"""Real hardware adapters. Thin wrappers over proto/transport.py, proto/hekate.py
and mtp.py; tests replace this whole object with fakes."""
import struct
import time

from .common import KNOWN_DEVICES, NYX
from .mtp import MtpFiles, MtpMailbox


def is_remote_hekate(dev):
    """Stock hekate reuses 11ec:a7e1 for its HID gamepad gadget; Remote Hekate exposes vendor class 0xff/0x52/1."""
    try:
        return any((i.bInterfaceClass, i.bInterfaceSubClass, i.bInterfaceProtocol) == (0xff, 0x52, 1)
                   for cfg in dev for i in cfg)
    except Exception:
        return True


class UsbLinks:
    def __init__(self):
        self.mtp = MtpFiles()
        self.mailbox = MtpMailbox(self.mtp)

    def devices(self):
        import usb.core

        found = set()

        for dev in usb.core.find(find_all=True):
            key = f'{dev.idVendor:04x}:{dev.idProduct:04x}'

            if key in KNOWN_DEVICES and (key != NYX or is_remote_hekate(dev)):
                found.add(key)

        return found

    def open(self, target):
        if target == 'nyx':
            from .proto.hekate import Nyx

            return Nyx()

        from .proto.transport import UsbSocket

        return UsbSocket(connect_gdb=False, interface_number=0 if target == 'worker' else 1)

    def drain(self, target):
        if target == 'nyx':
            return drain_nyx()

        from .proto.transport import UsbSocket

        transport = UsbSocket(connect_gdb=False, interface_number=0 if target == 'worker' else 1)

        try:
            return transport.drain_pending_replies()
        finally:
            transport.close()

    def external_disks(self):
        from .disks import external_disks

        return external_disks()


def drain_nyx(seconds=3):
    import usb.core
    from .proto.hekate import Nyx

    nyx = Nyx()

    try:
        end = time.monotonic() + seconds
        count = quiet = 0

        while time.monotonic() < end:
            try:
                nyx.input.read(4096, timeout=250)
            except usb.core.USBTimeoutError:
                quiet += 1

                if quiet == 2:
                    return count

                continue

            quiet = 0
            count += 1

        raise TimeoutError('Remote Hekate replies did not quiesce')
    finally:
        nyx.close()


def build_mailbox_request(token_hex, sequence, now_ms, op, data):
    if len(data) > 80:
        raise ValueError('Mailbox payload is limited to 80 bytes (128-byte request)')

    request = bytearray(128)
    struct.pack_into('<8sQQQII', request, 0, b'SCMAIL01', int(token_hex, 16), sequence,
                     now_ms + 20000, op, len(data))
    request[40:40 + len(data)] = data
    digest = 14695981039346656037

    for byte in request[:120]:
        digest = ((digest ^ byte) * 1099511628211) & ((1 << 64) - 1)

    struct.pack_into('<Q', request, 120, digest)

    return bytes(request)

