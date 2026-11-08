#!/usr/bin/env python3
"""nro_check.py - docs/NRO_CHECKLIST.md as checks (D25 #11): every item a tool can verify, run on the device package
as published (the private branch nro/<commit12>, tools/farm/lib_job.sh publish_nro) or as built (a full build
directory). Items only the hardware run can show are listed as manual, with their owner. zero_targets.py's #11 row
reads the result.

    python3 -I tools/report/nro_check.py <dir> [--guest-elf fnvnx.elf] [--boot-json boot_summary.json]
                                         [--commit C] [--s4] --out nrocheck.md

<dir>: the nro/<commit12> tree (sd/, nro/fnv.elf, host/host.elf, build.json, MANIFEST.txt) or a full build directory
(package/sd, package/nro/fnv.elf, host/host.elf, guest/fnvnx.elf, boot/boot_summary.json). The guest ELF and the
boot summary are not on the nro branch (link/<commit12> has them): without them the memory peak and the guest's
instruction scan by ELF are `open`, never a pass.

Automated items (id: what; the tool or the rule):
  nro.format     NRO header, MOD0, dynamic table, segments, the ELF's permissions (tests/nx1/check_nro.py)
  nro.nacp       NACP 0x4000 bytes, name "fnv-nx" (the checklist's step 5), author, display version = the commit12
  nro.icon       256x256 baseline JPEG (hbmenu)
  nro.romfs      the asset RomFS parses (toolchain/tools/romfs.py), holds shaders.nxsp and only the port's files: no
                 GLSL, nothing of the player's game (.esm, .bsa, .exe, .sdp, translated.nxsp)
  exefs          fnv-exefs.nsp: PFS0 main + main.npdm, NPDM fields, both NSOs (tests/s5/check_package.py)
  sd.layout      the SD tree: the sdlayout.h files, only FNVNX_SD_PORT_NAMES under switch/fnv, nothing else on the
                 card, no player files (tests/nx1/check_fs.py --sd; --s4 adds its tests/s4 runtime cases)
  paths.policy   data and save path policy in the shipped images: every "sdmc:/..." path token inside FNVNX_SD_DIR or
                 Atmosphere's contents folder, or one of their parents (sdmc:/switch, sdmc:/atmosphere: the launcher
                 checks them); templates "sdmc:/%..." are listed; no system save-data mount
                 ("save:/"): saves and INIs live on the SD card under user/
  memory.budget  the packaged NPDM: 32BitNoReserved (2 GiB heap region) inside the ~3.2 GiB application pool;
                 FNVNX_MIN_APP_MEMORY between the heap region and the pool; host heap (largest backend) + host image
                 + guest image (guest.nso's FNXG size) + S10's window <= half the heap region
  memory.peak    tests/nx1/check_mem.py with the guest ELF and the boot summary (the boot peak to the first cell)
  threads.plan   the packaged NPDM: cores 0..3, default 0, priorities inside 0x1C..0x3B, the main thread's inside
                 them, stack a multiple of 4 KiB; tests/nx1/check_threads.py (placement over every input, the
                 literal threadCreate sites)
  imports        no DT_NEEDED in the NRO (in nro.format); guest.nso: no svc instruction in its text; host and
                 launcher: SVCs, services, instructions (tests/nx1/check_svc.py; the guest ELF when given)
  size.limits    every file on the card below FAT32's 4 GiB - 1 (the checklist allows FAT32); fnv.nro below 64 MiB
                 (hbmenu reads it whole), the guest image inside the 1 GiB small map
  strip.release  the files on the card are not ELF and their loaded images carry no symbol table or DWARF section
                 names; the ELF sidecars (nro/fnv.elf, host/host.elf: crash.log symbolization) have no allocated
                 .debug/.symtab section. The farm's package is the release profile: there is no other
  manifest       MANIFEST.txt (publish_nro) lists every file of sd/ with its size and sha256 (nro tree only)
  provenance     build.json's commit / --commit is the NACP's version (tests/nx1/nro_provenance.py's rule)
  checklist.page the owner's page itself (tests/nx1/check_checklist.py)

Metric: automated items not `pass` (fail + open). Manual items are listed with their owner and not counted.
Writes <out> and <out>.json (fnvnx-nrocheck/1). Exit 0; 3 when <dir> holds no package (metric null).
"""
import argparse
import io
import json
import os
import re
import struct
import subprocess
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
for d in ('tests/nx1', 'tests/s5', 'toolchain/tools'):
    sys.path.insert(0, os.path.join(P, d))
import build_pfs0  # noqa: E402
import check_nro  # noqa: E402
import elf2nso  # noqa: E402
import npdmtool  # noqa: E402
import nxelf  # noqa: E402
import romfs  # noqa: E402

MIB = 1 << 20
POOL = int(3.2 * 1024) * MIB            # the application pool under title takeover (docs/NRO.md)
REGION = {'32BitNoReserved': 2048 * MIB, '32Bit': 1024 * MIB, '64Bit': 6144 * MIB}
WINDOW = 0x02100000 - 0x00FDF000         # S10's window backing, taken off the heap (check_mem.py)
FAT32_MAX = 0xFFFFFFFF
NRO_MAX = 64 * MIB
SMALL_MAP = 1024 * MIB
PRIO = (0x1C, 0x3B)
ROMFS_OK = {'shaders.nxsp'}
GAME_FILE = re.compile(r'(?i)\.(esm|esp|bsa|exe|sdp)$|^translated\.(nxsp|json)$|\.(glsl|vert|frag)$')
PARENTS = {'sdmc:/', 'sdmc:/switch', 'sdmc:/switch/', 'sdmc:/atmosphere', 'sdmc:/atmosphere/',
           'sdmc:/atmosphere/contents', 'sdmc:/atmosphere/contents/'}
DEBUG_NAMES = (b'.debug_info\0', b'.debug_line\0', b'.debug_abbrev\0', b'.symtab\0', b'.strtab\0')

MANUAL = [
    ('console', 'a Switch with Atmosphere on system 5.0.0+', 'owner'),
    ('sd.card', 'an SD card with about 12 GB free, exFAT or FAT32; the copy of sd/switch/fnv/', 'owner'),
    ('game.files', 'the owner\'s FalloutNV.exe (GOG 1.4.0.525) and Data/ copied next to it', 'owner'),
    ('game.shaders', 'translated.nxsp built from the owner\'s Data/Shaders (toolchain/nro/shaders_nx.py; '
     '--verify checks coverage on the owner\'s side)', 'owner'),
    ('takeover', 'hold R on a game reaches hbmenu with the application pool (not the Album applet)', 'owner (hardware)'),
    ('launcher.lines', 'hbmenu lists fnv-nx and every launcher line says ok', 'owner (hardware)'),
    ('restart', 'A restarts the title with fnv-nx\'s process; the override is gone afterwards (logic: NX1\'s '
     'host_device_test under qemu)', 'owner (hardware), NX1'),
    ('display', 'the launcher\'s text screen, then the native renderer\'s display', 'owner (hardware), render'),
    ('exit', 'HOME, close the software: back to the HOME menu', 'owner (hardware)'),
    ('recovery', 'a crash leaves crash.log; Y removes a leftover override', 'owner (hardware)'),
    ('send.back', 'launcher.log, host.log, crash.log, a photo, docked/handheld and the versions', 'owner'),
]


class Pkg:
    def __init__(self, d, guest_elf=None, boot_json=None, commit=None):
        pick = lambda *rel: next((os.path.join(d, r) for r in rel if os.path.exists(os.path.join(d, r))), None)  # noqa
        self.dir = d
        self.sd = pick('sd', 'package/sd')
        self.fnv = os.path.join(self.sd, 'switch', 'fnv') if self.sd else None
        f = lambda n: os.path.join(self.fnv, n) if self.fnv and os.path.isfile(os.path.join(self.fnv, n)) else None  # noqa
        self.nro, self.exefs, self.guest = f('fnv.nro'), f('fnv-exefs.nsp'), f('guest.nso')
        self.nro_elf = pick('nro/fnv.elf', 'package/nro/fnv.elf')
        self.host_elf = pick('host/host.elf', 'package/host/host.elf')
        self.guest_elf = guest_elf or pick('guest/fnvnx.elf')
        self.boot_json = boot_json or pick('boot/boot_summary.json')
        self.manifest = pick('MANIFEST.txt')
        bj = pick('build.json', 'package/stage.json')
        rec = {}
        if bj:
            try:
                rec = json.load(open(bj))
            except ValueError:
                pass
        self.commit = commit or rec.get('commit') or rec.get('port_commit')


class Out:
    def __init__(self):
        self.items = []

    def add(self, iid, status, why, tool, owner='NX1'):
        self.items.append({'id': iid, 'status': status, 'why': why, 'tool': tool, 'owner': owner})


def run(argv):
    r = subprocess.run([sys.executable, '-I'] + argv, capture_output=True, text=True, errors='replace', cwd=P)
    lines = (r.stdout + r.stderr).strip().splitlines()
    fails = [l for l in lines if l.startswith('FAIL')]
    return r.returncode, fails, (lines[-1] if lines else 'rc %d' % r.returncode)


def script_item(o, iid, argv, tool, need=()):
    if any(not x for x in need):
        o.add(iid, 'open', 'input missing for %s' % tool, tool)
        return
    rc, fails, last = run(argv)
    o.add(iid, 'pass' if rc == 0 else 'fail', last if rc == 0 else '; '.join(fails[:4]) or last, tool)


def from_rows(o, iid, rows, prefixes, tool, extra=()):
    sel = [r for r in rows if r[0].startswith(prefixes)] + list(extra)
    bad = [r for r in sel if not r[1]]
    if not sel:
        o.add(iid, 'open', 'no %s checks ran' % iid, tool)
    else:
        o.add(iid, 'fail' if bad else 'pass', '; '.join('%s: %s' % (r[0], r[2]) for r in bad[:4]) or
              '%d checks' % len(sel), tool)


def aset(buf):
    """(icon, nacp, romfs) bytes of an NRO's assets, None without ASET"""
    size = struct.unpack_from('<I', buf, 0x18)[0]
    if buf[size:size + 4] != b'ASET':
        return None
    return [buf[size + o:size + o + s] for o, s in (struct.unpack_from('<QQ', buf, size + 8 + 16 * i) for i in range(3))]


def nro_image(buf):
    return buf[:struct.unpack_from('<I', buf, 0x18)[0]]


def nso_image(buf):
    return b''.join(s['bytes'] for s in elf2nso.parse_nso(buf)['segments'])


def images(pk):
    """{name: loaded image bytes} of the console binaries on the card"""
    out = {}
    if pk.nro:
        out['fnv.nro'] = nro_image(open(pk.nro, 'rb').read())
    if pk.exefs:
        b = open(pk.exefs, 'rb').read()
        for name, off, size in build_pfs0.parse_pfs0(b):
            if name == 'main':
                out['exefs:main'] = nso_image(b[off:off + size])
    if pk.guest:
        out['guest.nso'] = nso_image(open(pk.guest, 'rb').read())
    return out


def npdm_of(pk):
    if not pk.exefs:
        return None, None
    b = open(pk.exefs, 'rb').read()
    ents = {n: (o, s) for n, o, s in build_pfs0.parse_pfs0(b)}
    if 'main.npdm' not in ents:
        return None, None
    o, s = ents['main.npdm']
    host = elf2nso.parse_nso(b[ents['main'][0]:ents['main'][0] + ents['main'][1]]) if 'main' in ents else None
    return npdmtool.dump_npdm(b[o:o + s]), host


def kernel_flags(caps):
    for c in caps:
        ones = (~c & (c + 1)).bit_length() - 1
        if ones == 3:
            v = c >> 4
            return {'lowest_priority': v & 0x3F, 'highest_priority': (v >> 6) & 0x3F, 'min_core': (v >> 12) & 0xFF,
                    'max_core': (v >> 20) & 0xFF}
    return None


def nso_span(info):
    d = info['segments'][2]
    return (d['dst'] + d['size'] + d.get('bss', 0) + 0xFFF) & ~0xFFF


def host_heap():
    try:
        import check_mem
        h = check_mem.host_heaps()
        return max(v for v in h.values() if v) if any(h.values()) else None
    except Exception:
        return None


def sdlayout():
    t = open(os.path.join(P, 'toolchain', 'include', 'fnvnx', 'sdlayout.h')).read()
    d = re.search(r'#define FNVNX_SD_DIR\s+"([^"]+)"', t).group(1)
    names = ' '.join(re.findall(r'"([^"]*)"', re.search(r'#define FNVNX_SD_PORT_NAMES((?:.*\\\n)*.*)', t).group(1)))
    mm = re.search(r'#define\s+FNVNX_MIN_APP_MEMORY\s+\((\d+)ull\s*<<\s*20\)', t)
    return d, names.split(), int(mm.group(1)) * MIB if mm else None


def check_all(pk, s4=False):
    o = Out()
    rows = check_nro.check(pk.nro, pk.nro_elf, None, None, True).rows if pk.nro else []
    tool = 'tests/nx1/check_nro.py'
    if not pk.nro:
        for i in ('nro.format', 'nro.nacp', 'nro.icon', 'nro.romfs'):
            o.add(i, 'open', 'no fnv.nro in the package', tool)
    else:
        from_rows(o, 'nro.format', rows, ('header.', 'seg.', 'mod0.', 'dyn.', 'elf.'), tool)
        buf = open(pk.nro, 'rb').read()
        assets = aset(buf)
        extra = []
        if assets and len(assets[1]) >= 0x3070:
            n = assets[1]
            name = check_nro.cstr(n, 0, 0x200)
            ver = check_nro.cstr(n, 0x3060, 0x10)
            extra.append(('nacp.title', name == 'fnv-nx', 'name %r, the checklist says fnv-nx' % name))
            if pk.commit:
                extra.append(('nacp.commit', ver == pk.commit[:15] or (ver and pk.commit.startswith(ver)),
                              'display version %r, package commit %s' % (ver, pk.commit[:12])))
        from_rows(o, 'nro.nacp', rows, ('aset.nacp', 'nacp.', 'aset.version', 'aset.present'), tool, extra)
        from_rows(o, 'nro.icon', rows, ('aset.icon',), tool)
        if not assets or not assets[2]:
            o.add('nro.romfs', 'fail', 'no RomFS in fnv.nro: the host has no shader pack', 'toolchain/tools/romfs.py')
        else:
            try:
                files = romfs.read(assets[2])
                bad = sorted(n for n in files if n not in ROMFS_OK or GAME_FILE.search(n))
                why = []
                if 'shaders.nxsp' not in files:
                    why.append('no shaders.nxsp')
                if bad:
                    why.append('not the port\'s: %s' % ', '.join(bad[:5]))
                o.add('nro.romfs', 'fail' if why else 'pass', '; '.join(why) or '%d file(s): %s' % (
                    len(files), ', '.join(sorted(files))), 'toolchain/tools/romfs.py')
            except Exception as e:
                o.add('nro.romfs', 'fail', 'RomFS does not parse: %s' % e, 'toolchain/tools/romfs.py')

    script_item(o, 'exefs', ['tests/s5/check_package.py', pk.sd or ''], 'tests/s5/check_package.py', need=(pk.exefs,))
    script_item(o, 'sd.layout', ['tests/nx1/check_fs.py', '--sd', pk.sd or ''] + ([] if s4 else ['--no-s4']),
                'tests/nx1/check_fs.py', need=(pk.sd,))
    sd_dir, port_names, min_mem = sdlayout()
    if pk.sd:
        rel = sd_dir[len('sdmc:/'):]
        stray = []
        for root, _ds, fs in os.walk(pk.sd):
            for fn in fs:
                r = os.path.relpath(os.path.join(root, fn), pk.sd)
                top = r[len(rel) + 1:].split('/')[0] if r.startswith(rel + '/') else None
                if top is None or not any(top == n or (n.endswith('*') and top.startswith(n[:-1])) for n in port_names) \
                        or GAME_FILE.search(top):
                    stray.append(r)
        if stray:
            o.items[-1].update(status='fail', why='not the port\'s files: %s; ' % ', '.join(stray[:5]) + o.items[-1]['why'])

    imgs = {}
    try:
        imgs = images(pk)
    except Exception as e:
        o.add('paths.policy', 'fail', 'images do not parse: %s' % e, 'nro_check.py')
    if imgs:
        bad, tmpl, saves = [], set(), []
        for nm, b in imgs.items():
            for m in re.finditer(rb'sdmc:/[\x21-\x7e]*', b):   # the path token: up to a space or a NUL
                s = m.group(0).decode().rstrip('.,;:\'")')
                if s.startswith(sd_dir + '/') or s == sd_dir or s.startswith('sdmc:/atmosphere/contents/'):
                    continue
                if s in PARENTS:   # the launcher checks or creates them (Atmosphere present, /switch exists)
                    continue
                if re.match(r'sdmc:/(%|$)', s):
                    tmpl.add(s)
                    continue
                bad.append('%s: %s' % (nm, s[:60]))
            if re.search(rb'\bsave:/', b):
                saves.append(nm)
        why = []
        if bad:
            why.append('outside %s: %s' % (sd_dir, '; '.join(bad[:4])))
        if saves:
            why.append('system save-data mount in %s (saves belong under user/)' % ', '.join(saves))
        o.add('paths.policy', 'fail' if why else 'pass', '; '.join(why) or '%d image(s); templates: %s' % (
            len(imgs), ', '.join(sorted(tmpl)) or '-'), 'nro_check.py (sdlayout.h)')
    elif not any(i['id'] == 'paths.policy' for i in o.items):
        o.add('paths.policy', 'open', 'no console binaries in the package', 'nro_check.py')

    npdm, host = (None, None)
    try:
        npdm, host = npdm_of(pk)
    except Exception as e:
        o.add('memory.budget', 'fail', 'exefs does not parse: %s' % e, 'nro_check.py')
    guest_span = None
    if pk.guest:
        try:
            t = elf2nso.parse_nso(open(pk.guest, 'rb').read())['segments'][0]['bytes']
            if struct.unpack_from('<I', t, 8)[0] == elf2nso.GUEST_MAGIC:
                guest_span = struct.unpack_from('<I', t, 20)[0]
        except Exception:
            pass
    if npdm:
        why, info = [], []
        region = REGION.get(npdm['address_space'])
        if npdm['address_space'] != '32BitNoReserved':
            why.append('address space %s, D8 wants 32BitNoReserved' % npdm['address_space'])
        region = region or 2048 * MIB
        if region > POOL:
            why.append('heap region %d MiB > the %d MiB pool' % (region // MIB, POOL // MIB))
        if not min_mem or not region <= min_mem <= POOL:
            why.append('FNVNX_MIN_APP_MEMORY %s MiB not in %d..%d MiB' % (min_mem and min_mem // MIB, region // MIB,
                                                                        POOL // MIB))
        hh = host_heap()
        hs = nso_span(host) if host else None
        parts = {'host heap': hh, 'host image': hs, 'guest image': guest_span, 'window': WINDOW}
        miss = [k for k, v in parts.items() if v is None]
        if miss:
            why.append('unknown: %s' % ', '.join(miss))
        else:
            fixed = sum(parts.values())
            info.append(' + '.join('%s %d' % (k, v // MIB) for k, v in parts.items()) + ' = %d MiB of %d' % (
                fixed // MIB, region // MIB))
            if fixed > region // 2:
                why.append('fixed %d MiB > half the heap region' % (fixed // MIB))
            info.append('guest heap left %d MiB' % ((region - fixed) // MIB))
        o.add('memory.budget', 'fail' if why else 'pass', '; '.join(why + info), 'nro_check.py (main.npdm, guest.nso)')
    elif not any(i['id'] == 'memory.budget' for i in o.items):
        o.add('memory.budget', 'open', 'no main.npdm in the package', 'nro_check.py')
    script_item(o, 'memory.peak', ['tests/nx1/check_mem.py', '--guest-elf', pk.guest_elf or '', '--boot-json',
                                   pk.boot_json or ''], 'tests/nx1/check_mem.py', need=(pk.guest_elf, pk.boot_json))
    if not pk.guest_elf or not pk.boot_json:
        o.items[-1]['why'] = 'needs the guest ELF and the boot summary (link/<commit12>; --guest-elf, --boot-json)'

    if npdm:
        kf, why = kernel_flags(npdm['kernel_caps']), []
        if not kf:
            why.append('no kernel_flags')
        else:
            if (kf['min_core'], kf['max_core']) != (0, 3) or npdm['default_cpu_id'] != 0:
                why.append('cores %d..%d default %d (want 0..3, default 0)' % (kf['min_core'], kf['max_core'],
                                                                            npdm['default_cpu_id']))
            if not (PRIO[0] <= kf['highest_priority'] <= kf['lowest_priority'] <= PRIO[1]):
                why.append('priorities %d..%d outside %d..%d' % (kf['highest_priority'], kf['lowest_priority'], *PRIO))
            if not kf['highest_priority'] <= npdm['main_thread_priority'] <= kf['lowest_priority']:
                why.append('main priority %d' % npdm['main_thread_priority'])
        if npdm['main_thread_stack_size'] % 0x1000:
            why.append('main stack %#x' % npdm['main_thread_stack_size'])
        rc, fails, last = run(['tests/nx1/check_threads.py'] + (['--host-elf', pk.host_elf] if pk.host_elf else []))
        if rc:
            why.append('check_threads: ' + ('; '.join(fails[:3]) or last))
        o.add('threads.plan', 'fail' if why else 'pass', '; '.join(why) or 'NPDM cores 0..3, priorities %d..%d; %s' % (
            kf['highest_priority'], kf['lowest_priority'], last), 'nro_check.py + tests/nx1/check_threads.py')
    else:
        o.add('threads.plan', 'open', 'no main.npdm in the package', 'nro_check.py')

    why, notes = [], []
    if 'guest.nso' in imgs:
        g = elf2nso.parse_nso(open(pk.guest, 'rb').read())['segments'][0]['bytes']
        n = sum(1 for k in range(0, len(g) - 3, 4) if struct.unpack_from('<I', g, k)[0] & 0xFFE0001F == 0xD4000001)
        if n:
            why.append('guest.nso text has %d svc words (the guest reaches the kernel only through the host)' % n)
    else:
        why.append('no guest.nso')
    if pk.host_elf and pk.nro_elf:
        argv = ['tests/nx1/check_svc.py', '--host-elf', pk.host_elf, '--nro-elf', pk.nro_elf]
        if pk.guest_elf:
            argv += ['--guest-elf', pk.guest_elf]
        rc, fails, last = run(argv)
        if rc:
            why.append('check_svc: ' + ('; '.join(fails[:3]) or last))
        notes.append(last)
        o.add('imports', 'fail' if why else 'pass', '; '.join(why) or '; '.join(notes), 'tests/nx1/check_svc.py')
    else:
        o.add('imports', 'fail' if why else 'open', '; '.join(why + ['the host and launcher ELFs are missing']),
              'tests/nx1/check_svc.py')

    why, sizes = [], {}
    if pk.sd:
        for root, _ds, fs in os.walk(pk.sd):
            for fn in fs:
                p = os.path.join(root, fn)
                sizes[os.path.relpath(p, pk.sd)] = os.path.getsize(p)
        why += ['%s is %d bytes (FAT32 max %d)' % (k, v, FAT32_MAX) for k, v in sizes.items() if v > FAT32_MAX]
        if pk.nro and os.path.getsize(pk.nro) > NRO_MAX:
            why.append('fnv.nro %d MiB > %d MiB' % (os.path.getsize(pk.nro) // MIB, NRO_MAX // MIB))
        if guest_span and guest_span > SMALL_MAP:
            why.append('guest image %d MiB > the 1 GiB small map' % (guest_span // MIB))
        o.add('size.limits', 'fail' if why else 'pass', '; '.join(why) or 'package %d MiB in %d files, largest %s' % (
            sum(sizes.values()) // MIB, len(sizes), max(sizes, key=sizes.get) if sizes else '-'), 'nro_check.py')
    else:
        o.add('size.limits', 'open', 'no SD tree', 'nro_check.py')

    why = []
    if pk.fnv:
        for fn in sorted(os.listdir(pk.fnv)):
            p = os.path.join(pk.fnv, fn)
            if os.path.isfile(p) and open(p, 'rb').read(4) == b'\x7fELF':
                why.append('%s is an ELF' % fn)
    for nm, b in imgs.items():
        hit = [d.rstrip(b'\0').decode() for d in DEBUG_NAMES if d in b]
        if hit:
            why.append('%s image holds %s' % (nm, ', '.join(hit)))
    for side in (pk.nro_elf, pk.host_elf):
        if side:
            try:
                e = nxelf.read(side)
                alloc = [s.name for s in e.shdrs if s.flags & 2 and (s.name.startswith('.debug') or s.name == '.symtab')]
                if alloc:
                    why.append('%s: allocated %s' % (os.path.basename(side), ', '.join(alloc)))
            except Exception as ex:
                why.append('%s: %s' % (os.path.basename(side), ex))
    o.add('strip.release', 'fail' if why else ('pass' if imgs else 'open'),
          '; '.join(why) or ('%d image(s) without symbols or DWARF' % len(imgs) if imgs else 'no images'), 'nro_check.py')

    if pk.manifest and pk.sd:
        import hashlib
        listed, why = {}, []
        for line in open(pk.manifest):
            f = line.rstrip('\n').split('\t')
            if len(f) == 3:
                listed[f[0]] = (int(f[1]), f[2])
        for rel_, size in sizes.items():
            want = listed.pop(rel_, None)
            got = (size, hashlib.sha256(open(os.path.join(pk.sd, rel_), 'rb').read()).hexdigest())
            if want != got:
                why.append('%s %s' % (rel_, 'not listed' if want is None else 'differs'))
        why += ['%s listed, not in sd/' % k for k in listed]
        o.add('manifest', 'fail' if why else 'pass', '; '.join(why[:5]) or '%d files match' % len(sizes),
              'MANIFEST.txt (publish_nro)')
    elif os.path.isdir(os.path.join(pk.dir, 'package')):
        pass   # a build directory: publish_nro writes the manifest
    else:
        o.add('manifest', 'open', 'no MANIFEST.txt (the nro/<commit12> tree has one)', 'MANIFEST.txt (publish_nro)')

    if pk.commit and pk.nro:
        a = aset(open(pk.nro, 'rb').read())
        ver = check_nro.cstr(a[1], 0x3060, 0x10) if a and len(a[1]) >= 0x3070 else None
        ok = bool(ver) and pk.commit.startswith(ver)
        o.add('provenance', 'pass' if ok else 'fail', 'NACP version %r, commit %s' % (ver, pk.commit[:12]),
              'tests/nx1/nro_provenance.py rule')
    else:
        o.add('provenance', 'open', 'no commit (build.json, package/stage.json or --commit)', 'nro_provenance.py rule')

    script_item(o, 'checklist.page', ['tests/nx1/check_checklist.py'], 'tests/nx1/check_checklist.py')
    for iid, what, owner in MANUAL:
        o.add('manual.' + iid, 'manual', what, 'hardware run (docs/NRO_CHECKLIST.md)', owner)
    return o


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('--guest-elf')
    ap.add_argument('--boot-json')
    ap.add_argument('--commit')
    ap.add_argument('--s4', action='store_true')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    pk = Pkg(a.dir, a.guest_elf, a.boot_json, a.commit)
    if not pk.sd:
        o = Out()
        have = False
    else:
        with redirect_stdout(io.StringIO()):
            o = check_all(pk, a.s4)
        have = True
    auto = [i for i in o.items if i['status'] != 'manual']
    cnt = {s: sum(1 for i in auto if i['status'] == s) for s in ('pass', 'fail', 'open')}
    head = {'schema': 'fnvnx-nrocheck/1', 'metric': cnt['fail'] + cnt['open'] if have else None, 'automated': len(auto),
            'pass': cnt['pass'], 'fail': cnt['fail'], 'open': cnt['open'],
            'manual': sum(1 for i in o.items if i['status'] == 'manual'), 'items': o.items,
            'commit': (pk.commit or '')[:12] or None, 'dir': os.path.basename(os.path.abspath(a.dir))}
    w = open(a.out, 'w')
    w.write('# NRO checklist (D25 #11)\n\n')
    if not have:
        w.write('- no package in %s (no sd/ or package/sd)\n' % a.dir)
    w.write('- metric %s: automated items not passing (fail %d, open %d) of %d; manual %d (hardware run)\n' % (
        head['metric'], cnt['fail'], cnt['open'], len(auto), head['manual']))
    if o.items:
        w.write('\n| item | status | owner | detail | tool |\n|---|---|---|---|---|\n')
        order = {'fail': 0, 'open': 1, 'pass': 2, 'manual': 3}
        for i in sorted(o.items, key=lambda i: order[i['status']]):
            w.write('| %s | %s | %s | %s | %s |\n' % (i['id'], i['status'], i['owner'], i['why'].replace('|', '/')[:200],
                                                    i['tool']))
    w.close()
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('nro_check: metric %s (pass %d, fail %d, open %d; manual %d)' % (head['metric'], cnt['pass'], cnt['fail'],
                                                                         cnt['open'], head['manual']))
    return 0 if have else 3


if __name__ == '__main__':
    sys.exit(main())
