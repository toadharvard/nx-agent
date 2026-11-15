#!/usr/bin/env python3
"""Self-contained check of link/milestones.py: a synthetic PE, modules.tsv, N1 certificate, keys.tsv and entries.tsv."""
import hashlib, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import milestones

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def pe(entry, base=0x400000):
    d = bytearray(0x400)
    d[:2] = b'MZ'
    struct.pack_into('<I', d, 0x3c, 0x40)
    d[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', d, 0x44, 0x14c, 1, 0, 0, 0, 224, 0x102)
    o = 0x58
    struct.pack_into('<HBB', d, o, 0x10b, 0, 0)
    struct.pack_into('<I', d, o + 16, entry)
    struct.pack_into('<I', d, o + 28, base)
    struct.pack_into('<II', d, o + 32, 0x1000, 0x200)
    struct.pack_into('<II', d, o + 56, 0x2000, 0x200)
    struct.pack_into('<I', d, o + 92, 16)
    struct.pack_into('<8sIIIIIIHHI', d, o + 224, b'.text', 0x1000, 0x1000, 0x200, 0x200, 0, 0, 0, 0, 0x60000020)
    return bytes(d)


TID = 'ab' * 32
exe = pe(0x1234)
mods = ('# header\nb.dll\t00\t1\t10000000\t01440000\t00068000\t0\t00005fab\t1\t1\n'
        'a.dll\t00\t1\t10000000\t01410000\t00029000\t0\t00001b0f\t0\t\n'
        'z.dll\t00\t1\t10000000\t01500000\t00029000\t0\t00000000\t1\t2\n')
keys = '# table_id %s\n00100980\t!idirect3ddevice9.dll\tPresent\t17\tstdcall\ti\tiiiii\tnxb_x\n' % TID
ents = '00100980\tI_1\timport\n# table_id %s\n' % TID


def n1(*fns):
    return {'exe_sha256': hashlib.sha256(exe).hexdigest(),
            'fn': {a: {'n1': ok, 'sym': s} for a, s, ok in fns}}


want = ('exe_entry\texe_entry\t00401234\texe PE ImageBase+AddressOfEntryPoint\n'
        'module_entry:a.dll\tmodule_entry\t01411b0f\tmodules.tsv assigned_base+entry_rva\t0\n'
        'module_entry:b.dll\tmodule_entry\t01445fab\tmodules.tsv assigned_base+entry_rva\t1\n')
tail = 'import:!idirect3ddevice9.dll!Present\timport\t00100980\tkeys.tsv import key\n# table_id %s\n' % TID
wm = 'WinMain\twinmain\t00401000\tn1.json N1 function ?WinMain@@YGHPAUHINSTANCE__@@0PADH@Z\n'

# WinMain N1: row; not N1: no row (nothing substituted); a similar name is not WinMain
check('with WinMain', milestones.build(exe, mods, n1((0x401000, '?WinMain@@YGHPAUHINSTANCE__@@0PADH@Z', True)), keys, ents),
      want + wm + tail)
check('not N1', milestones.build(exe, mods, n1((0x401000, '?WinMain@@YGHXZ', False)), keys, ents), want + tail)
check('other name', milestones.build(exe, mods, n1((0x401000, '?RainMain@@YGHXZ', True), (0x401100, '_WinMainX@4', True)), keys, ents),
      want + tail)
check('deterministic', milestones.build(exe, mods, n1(), keys, ents), milestones.build(exe, mods, n1(), keys, ents))
for why, args in (('two WinMain', (exe, mods, n1((1, '?WinMain@@A', True), (2, '_WinMain@16', True)), keys, ents)),
                  ('table ids differ', (exe, mods, n1(), keys, ents.replace(TID, 'cd' * 32))),
                  ('other exe', (exe, mods, dict(n1(), exe_sha256='0' * 64), keys, ents))):
    try:
        milestones.build(*args)
        fails.append('accepted: ' + why)
    except ValueError:
        pass
for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
