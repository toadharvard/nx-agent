#!/usr/bin/env python3
"""Self-test of tools/report/nro_check.py (D25 #11) on a synthetic package: tests/nx1/fixture's ELF packed as fnv.nro
(icon, NACP, a RomFS with a stand-in shaders.nxsp), the same ELF as the exefs host with main.npdm from
toolchain/npdm/fnvnx-app.json, a hand-made uncompressed guest.nso with the FNXG header, MANIFEST.txt and build.json as
publish_nro writes them. Then one mutation per item. No game data.

    python3 -I tests/s15/test_nro_check.py
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(P, 'toolchain', 'tools'))
import build_pfs0  # noqa: E402
import elf2nro  # noqa: E402
import elf2nso  # noqa: E402
import npdmtool  # noqa: E402
import nxelf  # noqa: E402
import romfs  # noqa: E402

TOOL = os.path.join(P, 'tools', 'report', 'nro_check.py')
C12 = '0123456789ab'
FAILS = 0


def ok(name, cond, info=''):
    global FAILS
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': %s' % (info,))[:600])
    FAILS += not cond


def nso(text, ro, data, bss):
    """an uncompressed NSO (flags: hashes only), segments page aligned"""
    pg = lambda n: (n + 0xFFF) & ~0xFFF   # noqa: E731
    segs, dst = [], 0
    for b in (text, ro, data):
        segs.append((dst, b))
        dst += pg(len(b))
    hdr = bytearray(0x100)
    struct.pack_into('<4sIII', hdr, 0, b'NSO0', 0, 0, 0x38)
    off = 0x100
    for i, (d, b) in enumerate(segs):
        struct.pack_into('<IIII', hdr, 0x10 + 0x10 * i, off, d, len(b), bss if i == 2 else 1)
        struct.pack_into('<I', hdr, 0x60 + 4 * i, len(b))
        hdr[0xA0 + 0x20 * i:0xC0 + 0x20 * i] = hashlib.sha256(b).digest()
        off += len(b)
    return bytes(hdr) + b''.join(b for _, b in segs)


def guest(abi, extra=b''):
    text = bytearray(0x1000)
    struct.pack_into('<IIIII', text, 8, elf2nso.GUEST_MAGIC, abi, 0, 0, 1)
    text[0x100:0x100 + len(extra)] = extra
    ro, data, bss = b'\0' * 0x1000, b'\0' * 0x1000, 0x2000
    struct.pack_into('<I', text, 20, 0x3000 + bss)
    return nso(bytes(text), ro, data, bss)


def hostapi():
    import re
    t = open(os.path.join(P, 'toolchain', 'include', 'fnvnx', 'hostapi.h')).read()
    return int(re.search(r'#define FNVNX_HOSTAPI_VERSION\s+(\d+)u', t).group(1))


def make(t, elf, *, name='fnv-nx', ver=C12, rom=None, npdm_edit=None, guest_extra=b'', stray=None, manifest=True,
         sd_elf=False, commit=C12 + '0' * 28):
    d = os.path.join(t, 'pkg')
    shutil.rmtree(d, ignore_errors=True)
    fnv = os.path.join(d, 'sd', 'switch', 'fnv')
    os.makedirs(fnv)
    os.makedirs(os.path.join(d, 'nro'))
    os.makedirs(os.path.join(d, 'host'))
    e = nxelf.read(elf)
    icon = open(os.path.join(P, 'toolchain', 'nro', 'icon.jpg'), 'rb').read()
    nacp = elf2nro.build_nacp(name, 'fnv-nx (bring your own game)', ver[:15])
    rf = romfs.build(rom if rom is not None else {'shaders.nxsp': b'NXSP' + b'\0' * 60})
    open(os.path.join(fnv, 'fnv.nro'), 'wb').write(elf2nro.build_nro(e, icon, nacp, rf))
    j = json.load(open(os.path.join(P, 'toolchain', 'npdm', 'fnvnx-app.json')))
    if npdm_edit:
        npdm_edit(j)
    nsp = build_pfs0.build_pfs0([('main', elf2nso.build_nso(e, compress=False)), ('main.npdm', npdmtool.build_npdm(j))])
    open(os.path.join(fnv, 'fnv-exefs.nsp'), 'wb').write(nsp)
    open(os.path.join(fnv, 'guest.nso'), 'wb').write(guest(hostapi(), guest_extra))
    open(os.path.join(fnv, 'README.txt'), 'w').write('fnv-nx\n')
    if stray:
        p = os.path.join(d, 'sd', stray)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b'x')
    if sd_elf:
        shutil.copy(elf, os.path.join(fnv, 'fnv.elf'))
    shutil.copy(elf, os.path.join(d, 'nro', 'fnv.elf'))
    shutil.copy(elf, os.path.join(d, 'host', 'host.elf'))
    if manifest:
        with open(os.path.join(d, 'MANIFEST.txt'), 'w') as m:
            for root, _ds, fs in sorted(os.walk(os.path.join(d, 'sd'))):
                for fn in sorted(fs):
                    p = os.path.join(root, fn)
                    m.write('%s\t%d\t%s\n' % (os.path.relpath(p, os.path.join(d, 'sd')), os.path.getsize(p),
                                              hashlib.sha256(open(p, 'rb').read()).hexdigest()))
    json.dump({'commit': commit, 'schema': 'fnvnx-nro-build/1'}, open(os.path.join(d, 'build.json'), 'w'))
    return d


def check(d, *extra):
    out = os.path.join(os.path.dirname(d), 'nrocheck.md')
    r = subprocess.run([sys.executable, '-I', TOOL, d, '--out', out] + list(extra), capture_output=True, text=True,
                       timeout=600)
    j = json.load(open(out[:-3] + '.json')) if os.path.isfile(out[:-3] + '.json') else {}
    return r, j, {i['id']: i for i in j.get('items', [])}, open(out).read() if os.path.isfile(out) else ''


def main():
    if not shutil.which('aarch64-linux-gnu-as'):
        print('SKIP: aarch64-linux-gnu binutils not installed')
        return 0
    t = tempfile.mkdtemp(prefix='nrocheck-')
    try:
        elf = os.path.join(t, 'fx.elf')
        subprocess.run([os.path.join(P, 'tests', 'nx1', 'fixture', 'build.sh'), elf], check=True)
        r, j, it, md = check(make(t, elf))
        ok('runs', r.returncode == 0 and j.get('schema') == 'fnvnx-nrocheck/1', r.stdout + r.stderr)
        for k in ('nro.format', 'nro.nacp', 'nro.icon', 'nro.romfs', 'sd.layout', 'paths.policy', 'memory.budget',
                  'size.limits', 'strip.release', 'manifest', 'provenance', 'checklist.page'):
            ok('good package: %s passes' % k, it.get(k, {}).get('status') == 'pass', it.get(k))
        ok('memory.peak open without the guest ELF and boot summary', it['memory.peak']['status'] == 'open',
           it['memory.peak'])
        ok('manual items with owners', j['manual'] >= 8 and all(i['owner'] for i in j['items'] if i['status'] == 'manual'))
        ok('metric = fail + open', j['metric'] == j['fail'] + j['open'] and j['automated'] == j['pass'] + j['metric'])
        ok('report table', '| item | status | owner |' in md and 'manual.takeover' in md)

        def bad(label, key, **kw):
            _r, _j, it2, _m = check(make(t, elf, **kw))
            ok('%s -> %s fails' % (label, key), it2.get(key, {}).get('status') == 'fail', it2.get(key))
        bad('NACP name', 'nro.nacp', name='other')
        bad('NACP version', 'nro.nacp', ver='ffffffffffff')
        bad('version vs commit', 'provenance', ver='ffffffffffff')
        bad('game file in the RomFS', 'nro.romfs', rom={'shaders.nxsp': b'x', 'FalloutNV.esm': b'x'})
        bad('no shader pack', 'nro.romfs', rom={'other.bin': b'x'})
        bad('address space', 'memory.budget', npdm_edit=lambda j: j.update(address_space_type='64bit'))
        bad('cores', 'threads.plan', npdm_edit=lambda j: j['kernel_capabilities'][0]['value'].update(highest_cpu_id=2))
        bad('priorities', 'threads.plan',
            npdm_edit=lambda j: j['kernel_capabilities'][0]['value'].update(lowest_thread_priority=63))
        bad('svc in the guest', 'imports', guest_extra=struct.pack('<I', 0xD4000001 | (0x26 << 5)))
        bad('path outside the layout', 'paths.policy', guest_extra=b'sdmc:/fnvnx/game/Data\0')
        bad('save-data mount', 'paths.policy', guest_extra=b'save:/FalloutNV\0')
        bad('DWARF in an image', 'strip.release', guest_extra=b'.debug_info\0')
        bad('ELF on the card', 'strip.release', sd_elf=True)
        bad('stray file on the card', 'sd.layout', stray='switch/fnv/Data/FalloutNV.esm')
        bad('file outside switch/fnv', 'sd.layout', stray='atmosphere/contents/x/exefs.nsp')
        d = make(t, elf)
        with open(os.path.join(d, 'sd', 'switch', 'fnv', 'README.txt'), 'a') as f:
            f.write('changed\n')
        _r, _j, it2, _m = check(d)
        ok('changed file -> manifest fails', it2['manifest']['status'] == 'fail', it2['manifest'])
        os.remove(os.path.join(d, 'MANIFEST.txt'))
        _r, _j, it2, _m = check(d)
        ok('no manifest -> open', it2['manifest']['status'] == 'open', it2['manifest'])
        r, j, _it, _m = check(os.path.join(t, 'empty-dir-that-is-missing'))
        ok('no package: exit 3', r.returncode == 3 and j.get('metric') is None and j.get('automated') == 0, r.stdout)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('test_nro_check: %d failed' % FAILS)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
