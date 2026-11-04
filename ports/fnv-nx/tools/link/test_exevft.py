#!/usr/bin/env python3
"""Test of exevft.py (the player's exe vftables through MSVC RTTI) and of the slot-fill rule, on a synthetic PE32
we write here (hand-written classes, no game content).

    python3 tools/link/test_exevft.py

The image has a .text section and an .rdata section at FalloutNV.exe's addresses (0x00401000, 0x00fdf000), with,
per class: a TypeDescriptor `.?AV<C>@@`, a class hierarchy descriptor with its base class array, and per vftable a
complete object locator (COL: signature 0, the vfptr's offset, the TypeDescriptor, the hierarchy) followed by the
vftable's .text words. Checks:
* scan() finds exactly the vftables written, with class, COL offset, bases and slots, and not the decoys: a COL
  pointer followed by a data word, a "COL" whose signature is not 0, a TypeDescriptor that is not `.?A`;
* a vftable ends at the first word outside .text (the next vftable's COL pointer);
* msvc_name() gives the primary vftable's MSVC name, which llvm-undname spells "const C::`vftable'" (if installed);
* load() caches the scan and reuses it;
* fnvlink.exe_slot_index (the minus-one rule): GCC's D1/D0 pair is MSVC's one deleting destructor.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft  # noqa: E402

BASE = 0x00400000
TEXT_RVA, TEXT_SIZE = 0x1000, 0x2000
RDATA_RVA, RDATA_SIZE = 0x00fdf000 - BASE, 0x2000
FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


class RData:
    def __init__(self):
        self.buf = bytearray()

    def here(self):
        return BASE + RDATA_RVA + len(self.buf)

    def put(self, b, align=4):
        while len(self.buf) % align:
            self.buf += b'\0'
        a = self.here()
        self.buf += b
        return a

    def words(self, *ws):
        return self.put(struct.pack('<%dI' % len(ws), *[w & 0xffffffff for w in ws]))


def make_pe(path, build):
    """build(rdata) writes the .rdata content; returns the PE bytes written to path."""
    rd = RData()
    build(rd)
    rdata = bytes(rd.buf).ljust(RDATA_SIZE, b'\0')
    text = b'\xc3' * 0x200                       # ret; the slots point into .text, contents do not matter
    falign = 0x200
    hdr_size = 0x400
    text_off, rdata_off = hdr_size, hdr_size + len(text)
    size_of_image = RDATA_RVA + RDATA_SIZE
    mz = bytearray(0x40)
    mz[0:2] = b'MZ'
    struct.pack_into('<I', mz, 0x3c, 0x40)
    coff = struct.pack('<4sHHIIIHH', b'PE\0\0', 0x14c, 2, 0, 0, 0, 224, 0x0102)
    opt = bytearray(224)
    struct.pack_into('<HBB', opt, 0, 0x10b, 9, 0)
    struct.pack_into('<I', opt, 16, TEXT_RVA)                      # entry
    struct.pack_into('<III', opt, 28, BASE, 0x1000, falign)
    struct.pack_into('<HH', opt, 40, 5, 0)                         # OS version
    struct.pack_into('<HH', opt, 48, 5, 0)                         # subsystem version
    struct.pack_into('<III', opt, 56, size_of_image, hdr_size, 0)
    struct.pack_into('<H', opt, 68, 2)                             # GUI
    struct.pack_into('<I', opt, 92, 16)
    sec = b''
    sec += struct.pack('<8sIIIIIIHHI', b'.text', TEXT_SIZE, TEXT_RVA, len(text), text_off, 0, 0, 0, 0, 0x60000020)
    sec += struct.pack('<8sIIIIIIHHI', b'.rdata', RDATA_SIZE, RDATA_RVA, len(rdata), rdata_off, 0, 0, 0, 0,
                       0x40000040)
    head = bytes(mz) + coff + bytes(opt) + sec
    data = head.ljust(hdr_size, b'\0') + text + rdata
    with open(path, 'wb') as f:
        f.write(data)
    return data


T = lambda k: BASE + TEXT_RVA + 0x10 * k    # noqa: E731  (slot targets in .text)


def build(rd, out):
    """Classes: Base (primary only), Multi : Base, Side (primary + secondary {for Side} at +8), Tmpl<int> (a
    template name), and decoys."""
    td = {}
    for name in ('.?AVBase@@', '.?AVSide@@', '.?AVMulti@@', '.?AV?$Tmpl@H@@', '.?AUBad@@'):
        td[name] = rd.put(struct.pack('<II', 0x0101d000, 0) + name.encode() + b'\0')

    def bcd(t, mdisp):
        return rd.words(td[t], 0, mdisp, 0xffffffff, 0, 0x40, 0)

    def chd(entries):
        bs = [bcd(t, m) for t, m in entries]
        arr = rd.words(*bs)
        return rd.words(0, 0, len(bs), arr)

    def vft(cls, off, h, slots, sig=0):
        col = rd.words(sig, off, 0, td[cls], h)
        rd.words(col)
        v = rd.here()
        rd.words(*slots)
        return v

    h_base = chd([('.?AVBase@@', 0)])
    h_side = chd([('.?AVSide@@', 0)])
    h_multi = chd([('.?AVMulti@@', 0), ('.?AVBase@@', 0), ('.?AVSide@@', 8)])
    h_tmpl = chd([('.?AV?$Tmpl@H@@', 0)])
    out['Base'] = vft('.?AVBase@@', 0, h_base, [T(1), T(2), T(3)])
    out['Side'] = vft('.?AVSide@@', 0, h_side, [T(4), T(5)])
    out['Multi'] = vft('.?AVMulti@@', 0, h_multi, [T(6), T(2), T(3), T(7)])
    out['Multi{Side}'] = vft('.?AVMulti@@', 8, h_multi, [T(8), T(5)])
    out['Tmpl'] = vft('.?AV?$Tmpl@H@@', 0, h_tmpl, [T(9)])
    # decoys: a COL with signature 1; a COL pointer followed by a data word; a "TypeDescriptor" named 'x'
    vft('.?AVBase@@', 0, h_base, [T(10)], sig=1)
    col = rd.words(0, 0, 0, td['.?AVBase@@'], h_base)
    rd.words(col, rd.here())
    bogus = rd.put(struct.pack('<II', 0, 0) + b'xyz\0')
    col = rd.words(0, 0, 0, bogus, h_base)
    rd.words(col, T(11))


def main():
    tmp = tempfile.mkdtemp(prefix='exevft-')
    try:
        exe = os.path.join(tmp, 'Synthetic.exe')
        where = {}
        make_pe(exe, lambda rd: build(rd, where))
        got = exevft.scan(exe)
        check(set(got) == set(where.values()), 'scan finds the 5 vftables and no decoy (%d found)' % len(got))
        b, m, ms, t = (got.get(where[k]) for k in ('Base', 'Multi', 'Multi{Side}', 'Tmpl'))
        check(b is not None and b.td == '.?AVBase@@' and b.off == 0 and b.slots == [T(1), T(2), T(3)],
              'Base: class, offset 0, its 3 slots (ends at the next COL pointer)')
        check(m is not None and m.td == '.?AVMulti@@' and m.off == 0 and len(m.slots) == 4 and
              [x[0] for x in m.bases] == ['.?AVMulti@@', '.?AVBase@@', '.?AVSide@@'] and m.bases[2][1] == 8,
              'Multi: primary vftable with the hierarchy (Side at mdisp 8)')
        check(ms is not None and ms.td == '.?AVMulti@@' and ms.off == 8 and ms.slots == [T(8), T(5)],
              'Multi {for Side}: COL offset 8')
        check(t is not None and t.td == '.?AV?$Tmpl@H@@', 'template TypeDescriptor kept')
        check(exevft.msvc_name('.?AVMulti@@') == '??_7Multi@@6B@', 'msvc_name of a plain class')
        und = shutil.which('llvm-undname') or shutil.which('llvm-undname-18')
        if und:
            r = subprocess.run([und], input='??_7?$Tmpl@H@@6B@\n', capture_output=True, text=True)
            check("Tmpl<int>::`vftable'" in r.stdout, 'llvm-undname spells the template vftable name')
        cache = os.path.join(tmp, 'cache', 'exe_vftables.json')
        a1 = exevft.load(exe, cache)
        check(os.path.isfile(cache) and a1 == got, 'load() scans and writes its cache')
        orig = exevft.scan
        exevft.scan = lambda p: (_ for _ in ()).throw(AssertionError('scanned again'))
        try:
            a2 = exevft.load(exe, cache)
            check(a2 == got, 'load() reuses the cache')
        except AssertionError:
            check(False, 'load() reuses the cache')
        finally:
            exevft.scan = orig
        check(exevft.exe_path(exe) == os.path.abspath(exe) and exevft.exe_path('none') in (None,) + tuple(
            p for p in (os.environ.get('FNVNX_EXE'), os.environ.get('PLAYER_EXE')) if p),
            'exe_path: explicit file; none falls through to the environment')
        # the minus-one rule: GCC slots [f, D1, D0, g] = MSVC [f, ??_E, g]
        si = exevft.exe_slot_index
        check([si(k, 1) for k in range(4)] == [0, 1, 1, 2] and [si(k, None) for k in range(3)] == [0, 1, 2],
              'exe_slot_index: D1 and D0 share the deleting destructor\'s slot, later slots move down by one')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
