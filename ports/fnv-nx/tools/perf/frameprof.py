#!/usr/bin/env python3
"""frameprof.py - dynamic instructions per subsystem per frame, and D3D9 calls by type per frame, for the game guest
running in S5's emulator (tests/s5/emu_guest.py). Stream S15-perf, docs/PERF.md section 4.

    frameprof.py <guest.nso> --elf <fnvnx.elf> [--units units.json] [--map fnvnx.map]
                 [--frame SYM|0xADDR]... [--mark SYM|0xADDR]... [--calls REGEX] [--nine-ids nine_proxy_ids.h]
                 [--top N] [--json OUT] [--max-intervals N] [emu_guest options: --file, --root, --load-base,
                 --skip-faulting-init, --budget]

How it counts: one Unicorn block hook over the guest text adds the block's instruction count (bytes / 4) to that
block; at the end every block is attributed to the function symbol at or below it (`nm -n` of the ELF, as the
emulator's crash traces do), every function to its original FalloutNV.exe address (S2's `__fnv_<addr>` alias at the
same address), the address to the decomp unit that defines it (`--units`, the decomp's build/units.json) and the unit
to a subsystem (path rules below). Native code without an image address (platform/, the guest runtime, newlib,
libstdc++, the D3D9 proxy) is classified by the object it came from in the link map (`--map`), else by name.

Intervals: every entry of a `--frame` function (e.g. the main loop `__fnv_0086e650`, or the device's Present)
closes one frame; every entry of a `--mark` function closes a named phase (boot validation: `main`, WinMain
`__fnv_0086a850`, InitHolder `__fnv_00aa2020`). Each interval reports its instructions by subsystem, its top
functions and its D3D9 calls.

D3D9 calls: entries of the functions matching `--calls` (default: the null device `Null_IDirect3D*` and the Nine
proxies `PxIDirect3D*`), counted per method; plus every `ext_call` the guest makes, decoded with `--nine-ids`
(render/nine's generated nine_proxy_ids.h) into interface::method.

Threads: each interval also counts instructions per emulated thread, with the thread's placement role, ideal core and
mask (toolchain/host/placement.h, as the emulated host placed it): `threads` in the JSON, which
tools/perf/frame_budget.py turns into per-core frame times.

Prints counts and symbol names only. The cost model in docs/PERF.md turns instructions into milliseconds.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tests', 's5'))
import emu_guest  # noqa: E402
from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE  # noqa: E402

# subsystem rules on the decomp unit path (first match wins)
UNIT_RULES = [
    ('havok', r'^src/havok/|/bshavok/|bhk'),
    ('render-gamebryo', r'/niconsolerenderer/|/nidx9|/nid3d'),
    ('render-bsshader', r'^src/bsshader/'),
    ('scene-graph', r'/corelibs/nimain/'),
    ('animation', r'/nianimation/|/fallout_shared/animation|actormover|/nikfm|morph'),
    ('particles', r'/niparticle/|particle'),
    ('speedtree', r'^src/speedtree/'),
    ('ai', r'^src/fallout/ai/|/pathfinding/|combat'),
    ('scripts', r'script|tesscript|conditionfunction'),
    ('ui', r'/interface/|^src/bsmenu/|/menus/|tile'),
    ('audio', r'^src/bsaudio/|audio|sound'),
    ('io-streaming', r'nistream|modelloader|/bssystem/|archive|ioman|queuedfile|tesfile'),
    ('world', r'tesobject|teswor|tes\.cpp|cell|tesform|extradata|^src/fallout_shared/'),
    ('game-other', r'^src/fallout/'),
    ('staging-unsplit', r'^src/staging/|^src/unmapped/'),
    ('generated-init', r'^src/generated/'),
    ('gamebryo-other', r'^src/gamebryo2.2/'),
]
# classes of native code without an image address: link-map object path or symbol name
NATIVE_RULES = [
    ('d3d9-proxy', r'nine_proxy|PxIDirect3D|nx_m_|null_d3d9|Null_IDirect3D|NullBase'),
    ('d3dx', r'/d3dx/|D3DX'),
    ('platform-win32', r'platform_win32|platform/win32|Interlocked|CriticalSection|TlsGetValue|w32_'),
    ('platform-crt', r'platform_crt|platform/crt|_fnv_crt'),
    ('platform-audio-input', r'platform_audio|platform_input'),
    ('loader', r'loader_src|loader/'),
    ('startup', r'startup_|/startup/'),
    ('boot', r'game_boot|/boot\.cpp|boot\.o$'),
    ('guest-runtime', r'toolchain/guest|libfnvnx-guest|fnvnx_(lock|unlock|cond|thread)'),
    ('libc', r'libc\.a|libm\.a|newlib|^(mem|str)[a-z]+$'),
    ('libstdc++', r'libstdc\+\+|libsupc\+\+|libgcc'),
    ('traps', r'traps\.o|__fnvnx_trap|fnvnx_trap'),
    ('bridges', r'zz_bridges|__fnvnx_br_'),
]
DEFAULT_CALLS = r'Null_IDirect3D|PxIDirect3D'


def resolve(emu, spec):
    """symbol name or 0xADDR (link address) -> runtime address"""
    if re.match(r'^0x[0-9a-fA-F]+$', spec):
        a = int(spec, 16)
    else:
        a = None
        for addr, names in zip(emu.sym_addrs, emu.sym_names):
            if spec in names:
                a = addr
                break
        if a is None:
            raise SystemExit('frameprof: no symbol %s in the ELF' % spec)
    return a - emu.link_base + emu.base if emu.relocatable else a


def parse_map(path):
    """GNU ld map -> sorted [(start, end, object path)] of input sections in .text"""
    out = []
    pend = None
    rx = re.compile(r'^\s+(\.text\S*)?\s*0x([0-9a-f]{8,16})\s+0x([0-9a-f]+)\s+(\S.*)$')
    for line in open(path, errors='replace'):
        if pend is not None and line.startswith('                '):
            m = re.match(r'^\s+0x([0-9a-f]{8,16})\s+0x([0-9a-f]+)\s+(\S.*)$', line)
            if m:
                a, s = int(m.group(1), 16), int(m.group(2), 16)
                if s:
                    out.append((a, a + s, m.group(3).strip()))
            pend = None
            continue
        pend = None
        m = re.match(r'^ (\.text\S*)$', line.rstrip('\n'))
        if m:
            pend = m.group(1)
            continue
        m = rx.match(line)
        if m and m.group(1):
            a, s = int(m.group(2), 16), int(m.group(3), 16)
            if s:
                out.append((a, a + s, m.group(4).strip()))
    out.sort()
    return out


def load_units(path):
    """original address -> unit source (functions of build/units.json)"""
    if not path:
        return {}
    d = json.load(open(path))
    at = {}
    for u in d['units']:
        for f in u['functions']:
            at.setdefault(int(f['address'], 16), u['source'])
    return at


def load_nine_ids(path):
    ids = {}
    if path:
        for line in open(path):
            m = re.match(r'#define NXM_(\w+?)_(\w+) (0x[0-9a-f]+)', line)
            if m:
                ids[int(m.group(3), 16)] = '%s::%s' % (m.group(1), m.group(2))
    return ids


class Prof(emu_guest.Emu):
    def prof_setup(self, a):
        self.counts = Counter()           # block runtime address -> executed instructions
        self.call_starts = {}
        self.call_counts = Counter()
        self.ext_counts = Counter()
        self.thread_counts = Counter()    # emulated thread id -> executed instructions
        self.intervals = []               # (label, counts snapshot, call snapshot, ext snapshot, thread snapshot)
        self.frame_no = 0
        self.max_intervals = a.max_intervals
        rx = re.compile(a.calls or DEFAULT_CALLS)
        for addr, names in zip(self.sym_addrs, self.sym_names):
            for n in names:
                if rx.search(n):
                    ra = addr - self.link_base + self.base if self.relocatable else addr
                    self.call_starts[ra] = n
                    break
        lo = self.base
        hi = self.base + len(self.text_bytes)
        self.uc.hook_add(UC_HOOK_BLOCK, self._on_block, begin=lo, end=hi - 1)
        for spec in a.frame:
            ad = resolve(self, spec)
            self.uc.hook_add(UC_HOOK_CODE, self._on_frame, user_data=('frame', spec), begin=ad, end=ad)
        for spec in a.mark:
            ad = resolve(self, spec)
            self.uc.hook_add(UC_HOOK_CODE, self._on_frame, user_data=('mark', spec), begin=ad, end=ad)

    def _on_block(self, uc, address, size, _):
        self.counts[address] += size >> 2
        self.thread_counts[self.cur.tid if self.cur else 1] += size >> 2
        n = self.call_starts.get(address)
        if n is not None:
            self.call_counts[n] += 1

    def _on_frame(self, uc, address, size, user):
        kind, spec = user
        if kind == 'frame':
            self.frame_no += 1
            label = 'frame %d (%s)' % (self.frame_no, spec)
        else:
            label = 'until %s' % spec
        self.close_interval(label)

    def close_interval(self, label):
        if len(self.intervals) >= self.max_intervals:
            return
        self.intervals.append((label, self.counts, self.call_counts, self.ext_counts, self.thread_counts))
        self.counts, self.call_counts, self.ext_counts, self.thread_counts = Counter(), Counter(), Counter(), Counter()

    def thread_info(self, tid):
        """(placement role, ideal core, mask) of an emulated thread; the main thread is role main"""
        t = self.threads.get(tid)
        pl = getattr(t, 'place', None) if t is not None else None
        if tid == 1 or not pl:
            return ('main', 0, 1) if tid == 1 else ('?', -1, 0)
        return (pl[0], pl[1], pl[2])

    def h_ext_call(self, module=0, fn=0, *rest):
        self.ext_counts[(module, fn)] += 1
        return super().h_ext_call(module, fn, *rest)


class Attributor:
    def __init__(self, emu, units, mapsecs):
        self.emu, self.units, self.mapsecs = emu, units, mapsecs
        self.map_starts = [s for s, _, _ in mapsecs]
        self.cache = {}

    def func(self, rt_addr):
        """runtime block address -> (function label, original address or None, link-map object or '')"""
        e = self.emu
        a = rt_addr - e.base + e.link_base if e.relocatable else rt_addr
        i = bisect.bisect_right(e.sym_addrs, a) - 1
        if i < 0:
            return ('?', None, '')
        key = e.sym_addrs[i]
        r = self.cache.get(key)
        if r is None:
            names = e.sym_names[i]
            orig = None
            for n in names:
                m = re.match(r'^__fnv_([0-9a-f]{8})$', n) or re.match(r'^__fnv_([0-9a-f]{8})_', n)
                if m:
                    orig = int(m.group(1), 16)
                    break
            native = [n for n in names if not n.startswith('__fnv_')]
            label = native[0] if native else names[0]
            obj = ''
            if self.mapsecs:
                j = bisect.bisect_right(self.map_starts, key) - 1
                if j >= 0 and self.mapsecs[j][0] <= key < self.mapsecs[j][1]:
                    obj = self.mapsecs[j][2]
            r = (label, orig, obj)
            self.cache[key] = r
        return r

    def subsystem(self, label, orig, obj):
        if orig is not None:
            src = self.units.get(orig)
            if src:
                for name, rx in UNIT_RULES:
                    if re.search(rx, src):
                        return name
                return 'decomp-other'
            return 'image-addr-no-unit'
        base = os.path.basename(obj)
        m = re.match(r'^(src_.*)\.o$', base)
        if m:                                       # a decomp object without an address for this symbol
            src = m.group(1).replace('_', '/')
            for name, rx in UNIT_RULES:
                if re.search(rx, src):
                    return name
            return 'decomp-other'
        for name, rx in NATIVE_RULES:
            if re.search(rx, obj) or re.search(rx, label):
                return name
        return 'native-other'


def report(emu, attr, intervals, nine_ids, top, out_json):
    res = []
    for label, counts, calls, ext, threads in intervals:
        per_fn = Counter()
        per_sub = Counter()
        for addr, n in counts.items():
            lab, orig, obj = attr.func(addr)
            key = lab if orig is None else '%s [%08x]' % (lab, orig)
            per_fn[key] += n
            per_sub[attr.subsystem(lab, orig, obj)] += n
        total = sum(per_sub.values())
        d3d = Counter()
        for n, k in calls.items():
            d3d[re.sub(r'^_ZN\d+', '', n)] += k
        for (module, fn), k in ext.items():
            d3d['ext_call %d:%s' % (module, nine_ids.get(fn, '%#06x' % fn))] += k
        print('== %s: %d instructions' % (label, total))
        for s, n in per_sub.most_common():
            print('   %-22s %12d  %5.1f %%' % (s, n, 100.0 * n / max(total, 1)))
        if top:
            print('   top functions:')
            for f, n in per_fn.most_common(top):
                print('     %12d  %5.1f %%  %s' % (n, 100.0 * n / max(total, 1), f[:110]))
        if d3d:
            print('   D3D9 calls: %d' % sum(d3d.values()))
            for f, n in d3d.most_common(40):
                print('     %8d  %s' % (n, f[:100]))
        th = {}
        for tid, n in sorted(threads.items()):
            role, ideal, mask = emu.thread_info(tid)
            th[str(tid)] = dict(role=role, ideal=ideal, mask=mask, instructions=n)
        if len(th) > 1:
            print('   threads: ' + ', '.join('%s %s %d' % (k, v['role'], v['instructions']) for k, v in th.items()))
        res.append(dict(label=label, instructions=total, subsystems=dict(per_sub),
                        top=per_fn.most_common(top), d3d9=dict(d3d), threads=th))
    if out_json:
        json.dump(res, open(out_json, 'w'), indent=1)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('nso')
    ap.add_argument('--elf', required=True)
    ap.add_argument('--units', help='decomp build/units.json (original address -> unit)')
    ap.add_argument('--map', help='guest link map (fnvnx.map): native code -> object')
    ap.add_argument('--frame', action='append', default=[])
    ap.add_argument('--mark', action='append', default=[])
    ap.add_argument('--calls', help='regex of D3D9 entry points to count (default %s)' % DEFAULT_CALLS)
    ap.add_argument('--nine-ids', help="render/nine's generated nine_proxy_ids.h, to name ext_call ids")
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--json')
    ap.add_argument('--max-intervals', type=int, default=1000)
    ap.add_argument('--root')
    ap.add_argument('--file', action='append', default=[])
    ap.add_argument('--load-base', type=lambda v: int(v, 0))
    ap.add_argument('--skip-faulting-init', action='store_true')
    ap.add_argument('--budget', type=int, default=400_000_000)
    a = ap.parse_args(argv)
    import shutil
    import tempfile
    root = a.root or tempfile.mkdtemp(prefix='fnvnx-prof-')
    for spec in a.file:
        name, _, src = spec.partition('=')
        dst = os.path.join(root, name.lstrip('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    emu = Prof(a.nso, root, load_base=a.load_base)
    emu.budget = a.budget
    emu.load_symbols(a.elf)
    if a.skip_faulting_init:
        emu.install_guards()
    emu.prof_setup(a)
    try:
        rc = emu.run()
        why = 'guest returned %#x' % rc
    except SystemExit as e:
        why = str(e)
    except Exception as e:      # noqa: BLE001 - report what was counted so far
        why = 'host stand-in raised %r' % e
    emu.close_interval('until stop (%s)' % why[:80])
    print('frameprof: stopped: %s' % why)
    attr = Attributor(emu, load_units(a.units), parse_map(a.map) if a.map else [])
    report(emu, attr, emu.intervals, load_nine_ids(a.nine_ids), a.top, a.json)
    if not a.root:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
