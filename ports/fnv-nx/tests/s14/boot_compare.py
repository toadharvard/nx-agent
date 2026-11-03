#!/usr/bin/env python3
"""Boot S5's emulated game guest on the all-zero data image and on S14's synthetic one, and compare.

    python3 -I tests/s14/boot_compare.py --nso guest.nso --elf fnvnx.elf --image synth.exe --state state.bin \\
        [--modes zero,prefill,pe] [--out DIR]

Modes (each a fresh emulator, tests/s5/emu_guest.py with --skip-faulting-init semantics):
  zero     no exe: the boot maps an empty window 0x00400000..0x02100000 (J-0032's traced boot)
  prefill  the same boot path, but the window is filled with the synthetic image's section bytes right after
           the boot zeroed it (before any static initializer runs): only the data differs from 'zero'
  pe       the synthetic PE32 is the player's exe (sdmc:/fnvnx/game/FalloutNV.exe): S3's loader maps it, binds
           the IAT through S4's exports (the synthetic import directory) and rewrites code pointers through
           S2's address map; 0x00400000..0x00fdf000 is mapped zeroed first, as S10 claims it on the console

Per run: initializers skipped after a fault (and which), unresolved-symbol traps, how far the boot got, and with
--state the image bytes the guest read before writing them during static initialization, by what the synthetic
image knows about them (synth_image.py --state-out). Output: <out>/<mode>.log and <out>/compare.json.
Everything printed is addresses and counts; the image itself stays where it is (decomp-derived, local only).
"""
import argparse
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 's5'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'loader', 'tools'))
import emu_guest  # noqa: E402
import fnvnx_pe  # noqa: E402
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE  # noqa: E402

WIN_LO, WIN_HI = 0x00fdf000, 0x02100000
STATE = {0: 'missing', 1: 'soft-zero', 2: 'unknown', 3: 'known', 4: 'bss', 5: 'iat', 6: 'no-section'}
PHASES = ('loader', 'gcc-init', 'xc-init', 'main', 'exit')


class SkipList(list):
    """emu_guest's list of skipped initializers; also keeps the window reads just before each fault"""

    def __init__(self, emu):
        super().__init__()
        self.emu = emu

    def append(self, x):
        super().append(x)
        self.emu.skip_ctx.append(list(self.emu.recent))
        self.emu.recent.clear()


class Run(emu_guest.Emu):
    def setup(self, mode, image, state):
        self.mode, self.image = mode, image
        self.phase = 'loader'
        self.filled = False
        self.first = bytearray(WIN_HI - WIN_LO)       # 1 read first, 2 written first
        self.first_phase = bytearray(WIN_HI - WIN_LO)
        self.state = state
        self.window_mapped = False
        self.phase_at = {}
        self.recent = collections.deque(maxlen=6)    # last window reads (address): context of a fault
        self.skip_ctx = []

    # data-window watch: first access per byte
    def on_read(self, uc, access, addr, size, value, _):
        if self.phase == 'loader':          # the boot zeroes / the loader fills the window: not the program
            return
        self.recent.append(addr)
        o = addr - WIN_LO
        f = self.first
        p = PHASES.index(self.phase) + 1
        for k in range(o, min(o + size, len(f))):
            if not f[k]:
                f[k] = 1
                self.first_phase[k] = p

    def on_write(self, uc, access, addr, size, value, _):
        if self.phase == 'loader':
            return
        o = addr - WIN_LO
        f = self.first
        p = PHASES.index(self.phase) + 1
        for k in range(o, min(o + size, len(f))):
            if not f[k]:
                f[k] = 2
                self.first_phase[k] = p

    def watch(self):
        self.uc.hook_add(UC_HOOK_MEM_READ, self.on_read, begin=WIN_LO, end=WIN_HI - 1)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write, begin=WIN_LO, end=WIN_HI - 1)

    def fill_from_pe(self):
        with open(self.image, 'rb') as f:
            data = f.read()
        pe = fnvnx_pe.PE(data)
        n = 0
        for s in pe.sections:
            if s.is_code or not s.raw_size:
                continue
            va = pe.image_base + s.rva
            raw = data[s.raw_off:s.raw_off + min(s.raw_size, s.vsize_eff)]
            if WIN_LO <= va and va + len(raw) <= WIN_HI:
                self.uc.mem_write(va, raw)
                n += len(raw)
        self.filled = n

    def h_console_write(self, b, n, *rest):
        r = super().h_console_write(b, n, *rest)
        text = self.console[-4096:].decode(errors='replace')
        if self.phase == 'loader' and 'running GCC initialisers' in text:
            self.phase = 'gcc-init'
            self.phase_at['gcc-init'] = self.calls
            if self.mode == 'prefill' and not self.filled:
                self.fill_from_pe()
        elif self.phase == 'gcc-init' and 'initializer #0 ' in text:
            self.phase = 'xc-init'
        elif self.phase in ('gcc-init', 'xc-init') and 'static initialisers done' in text:
            self.phase = 'main'
        elif self.phase == 'main' and ('WinMain returned' in text or 'STOP:' in text.split('static initialisers done')[-1]):
            self.phase = 'exit'
        return r

    def h_map_fixed(self, addr, size, src, *rest):
        return super().h_map_fixed(addr, size, src, *rest)


def run_mode(a, mode, out_dir, state):
    root = tempfile.mkdtemp(prefix='fnvnx-s14-')
    if mode == 'pe':
        dst = os.path.join(root, 'fnvnx', 'game', 'FalloutNV.exe')
        os.makedirs(os.path.dirname(dst))
        shutil.copyfile(a.image, dst)
    emu = Run(a.nso, root)
    emu.budget = a.budget
    emu.setup(mode, a.image, state)
    emu.load_symbols(a.elf)
    emu.install_guards()
    emu.skipped = SkipList(emu)
    if mode == 'pe':
        # S10 claims header + .text range as backed zero memory; the loader maps only the data sections
        emu.uc.mem_map(0x00400000, 0x00fdf000 - 0x00400000, UC_PROT_READ | UC_PROT_WRITE)
    if state is not None:
        emu.watch()
    t0 = time.time()
    stop = None
    log = open(os.path.join(out_dir, mode + '.log'), 'w')
    old = sys.stdout
    sys.stdout = log
    try:
        rc = emu.run()
    except SystemExit as e:
        stop = str(e)
        rc = None
    finally:
        sys.stdout = old
    con = emu.console.decode(errors='replace')
    log.write('\n==== console\n' + con + '\n==== stop: %s\n' % stop)
    log.write('==== skipped initializers\n')
    for tgt, pc in emu.skipped:
        log.write('%s <- %s\n' % (emu.sym(tgt), emu.sym(pc)))
    log.close()
    shutil.rmtree(root, ignore_errors=True)
    res = dict(mode=mode, seconds=round(time.time() - t0, 1), stop=stop, rc=rc, host_calls=emu.calls,
               skipped=len(emu.skipped), filled_bytes=emu.filled if mode == 'prefill' else None)
    res['skipped_targets'] = sorted({emu.sym(t) for t, _ in emu.skipped})
    res['skipped_fault_sites'] = dict(collections.Counter(emu.sym(pc).split(' ', 1)[-1].split('+')[0]
                                                          for _, pc in emu.skipped).most_common(15))
    for line in con.splitlines():
        if 'static initialisers done' in line:
            res['init_done'] = line.strip()
        if 'STOP:' in line:
            res['boot_stop'] = line.strip()
        if 'imports:' in line or 'code pointers:' in line or 'xc:' in line or 'image __xc' in line:
            res.setdefault('loader', []).append(line.strip())
    res['traps_distinct'] = sum(1 for line in con.splitlines() if 'trap #' in line)
    if state is not None:
        # what the guest last read from the window before each fault, by what the synthetic image knows
        ctx = collections.Counter()
        last = collections.Counter()
        for reads in emu.skip_ctx:
            kinds = [STATE[state[a - WIN_LO]] for a in reads]
            ctx['none' if not kinds else ('after-missing-read' if 'missing' in kinds else 'after-known-reads')] += 1
            if reads:
                last['0x%08x %s' % (reads[-1], STATE[state[reads[-1] - WIN_LO]])] += 1
        res['fault_context'] = dict(ctx)
        res['fault_last_window_read'] = dict(last.most_common(12))
    res['phase_reached'] = emu.phase
    if state is not None:
        res['first_access'] = access_stats(emu, state)
    return res


def access_stats(emu, state):
    """bytes of the window by first access (read first = depends on the image's initial value) and phase,
    classified by what the synthetic image knows"""
    import numpy as np
    f = np.frombuffer(bytes(emu.first), np.uint8)
    fp = np.frombuffer(bytes(emu.first_phase), np.uint8)
    st = np.frombuffer(state, np.uint8)
    out = {}
    for pi, ph in enumerate(PHASES):
        m = fp == pi + 1
        if not m.any():
            continue
        cnt = np.bincount(f[m].astype(np.int64) * 8 + st[m], minlength=24)
        out[ph] = {'%s/%s' % ('read' if k // 8 == 1 else 'write', STATE[k % 8]): int(n)
                   for k, n in enumerate(cnt) if n and k // 8 in (1, 2)}
        miss = np.nonzero(m & (f == 1) & (st == 0))[0]
        if len(miss):
            pages = np.bincount((miss + WIN_LO) >> 12)
            top = np.argsort(pages)[::-1][:10]
            out[ph + ':missing_read_pages'] = {'0x%08x' % (int(p) << 12): int(pages[p]) for p in top if pages[p]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--nso', required=True)
    ap.add_argument('--elf', required=True)
    ap.add_argument('--image', required=True, help='synth_image.py --out')
    ap.add_argument('--state', help='synth_image.py --state-out (classify the bytes the guest reads)')
    ap.add_argument('--modes', default='zero,prefill,pe')
    ap.add_argument('--out', default='build/s14')
    ap.add_argument('--budget', type=int, default=400_000_000)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    state = None
    if a.state:
        with open(a.state, 'rb') as f:
            state = f.read()
    results = []
    for mode in a.modes.split(','):
        r = run_mode(a, mode, a.out, state)
        results.append(r)
        print('%-8s %6.1fs skipped=%d traps=%d phase=%s stop=%s' % (mode, r['seconds'], r['skipped'],
                                                                 r['traps_distinct'], r['phase_reached'], r['stop']))
        for k in ('init_done', 'boot_stop'):
            if k in r:
                print('         ' + r[k])
        for line in r.get('loader', []):
            print('         ' + line)
    if len(results) > 1:
        base = set(results[0]['skipped_targets'])
        for r in results[1:]:
            s = set(r['skipped_targets'])
            r['vs_' + results[0]['mode']] = dict(fixed=len(base - s), new=len(s - base), same=len(base & s),
                                                 new_list=sorted(s - base)[:40], fixed_list=sorted(base - s)[:40])
            print('%-8s vs %s: %d skipped initializers no longer fault, %d new, %d both' % (
                r['mode'], results[0]['mode'], len(base - s), len(s - base), len(base & s)))
    with open(os.path.join(a.out, 'compare.json'), 'w') as f:
        json.dump(results, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
