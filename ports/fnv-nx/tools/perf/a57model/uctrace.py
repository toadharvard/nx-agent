#!/usr/bin/env python3
"""uctrace.py - the A57 model on the game guest: runs an ILP32 guest NSO in S5's emulator (tests/s5/emu_guest.py,
Unicorn, i.e. QEMU's TCG) and feeds its executed blocks and memory accesses into a57sim (liba57sim.so, the same
simulator as the QEMU plugin a57trace.so). Writes a57sim statistics for cost.py (docs/PERF.md 12).

    uctrace.py <guest.nso> --elf <fnvnx.elf> --out stats.txt [--lib liba57sim.so] [--threads main|all]
               [--start SYM|0xADDR] [--stop SYM|0xADDR] [--max-insns N] [--no-mem] [--params params.json]
               [emu_guest options: --file, --root, --load-base, --skip-faulting-init, --budget]

Block and instruction addresses are written as link addresses (the ELF's), so cost.py --elf names them; data
addresses stay the emulator's (the cache model needs only their spacing). --threads main (default) feeds the main
thread only, the frame's critical path; other threads' instructions are counted (summary line `other`).
--start/--stop: the region runs from the first entry of START (default: the first guest instruction) to the first
entry of STOP (default: the end). --max-insns stops the emulation after N main-thread instructions (local subsets;
the farm job runs it whole). --no-mem: blocks only (front end and core; no cache model), about 3x faster.
Prints counts only.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
S5 = os.path.join(HERE, '..', '..', '..', 'tests', 's5')
sys.path.insert(0, S5)
import emu_guest  # noqa: E402
from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE  # noqa: E402
from unicorn.arm64_const import UC_ARM64_REG_PC  # noqa: E402


def load_lib(path):
    if not path:
        out = os.path.join(HERE, 'build')
        path = os.path.join(out, 'liba57sim.so')
        src = os.path.join(HERE, 'a57sim.c')
        if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(src):
            os.makedirs(out, exist_ok=True)
            subprocess.run(['gcc', '-O2', '-fPIC', '-shared', '-o', path, src], check=True)
    L = ctypes.CDLL(path)
    L.a57sim_set.argtypes = [ctypes.c_char_p, ctypes.c_double]
    L.a57sim_block_h.restype = ctypes.c_uint64
    L.a57sim_block_h.argtypes = [ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32]
    L.a57sim_mem_h.restype = ctypes.c_uint64
    L.a57sim_mem_h.argtypes = [ctypes.c_uint64, ctypes.c_int]
    L.a57sim_block_exec_h.argtypes = [ctypes.c_uint64]
    L.a57sim_mem_access_h.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32]
    L.a57sim_dump.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    return L


def resolve(emu, spec):
    if spec.startswith('0x'):
        a = int(spec, 16)
    else:
        a = None
        for addr, names in zip(emu.sym_addrs, emu.sym_names):
            if spec in names:
                a = addr
                break
        if a is None:
            raise SystemExit(f'uctrace: no symbol {spec}')
    return a - emu.link_base + emu.base if emu.relocatable else a


class Trace(emu_guest.Emu):
    def trace_setup(self, a, L):
        self.L = L
        self.delta = (self.link_base - self.base) if self.relocatable else 0
        self.main_only = a.threads == 'main'
        self.blocks, self.mems = {}, {}
        self.feed = False
        self.blk_addr = 0
        self.main_insns = self.other_insns = 0
        self.max_insns = a.max_insns
        self.region = a.start is None
        self.stopped_by = None
        lo, hi = self.base, self.base + len(self.text_bytes)
        self.uc.hook_add(UC_HOOK_BLOCK, self._on_block, begin=lo, end=hi - 1)
        if not a.no_mem:
            self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._on_mem)
        if a.start:
            ad = resolve(self, a.start)
            self.uc.hook_add(UC_HOOK_CODE, self._on_mark, user_data='start', begin=ad, end=ad)
        if a.stop:
            ad = resolve(self, a.stop)
            self.uc.hook_add(UC_HOOK_CODE, self._on_mark, user_data='stop', begin=ad, end=ad)
        if self.region:
            L.a57sim_region(1)

    def _on_mark(self, uc, address, size, what):
        if what == 'start' and not self.region:
            self.region = True
            self.L.a57sim_region(1)
            self.main_insns = self.other_insns = 0
        elif what == 'stop' and self.region and not self.stopped_by:
            self.stopped_by = 'stop symbol'
            self.L.a57sim_region(0)
            self.region = False
            uc.emu_stop()
            raise SystemExit('uctrace: stop symbol reached')

    def _on_block(self, uc, address, size, _):
        n = size >> 2
        if self.main_only and self.cur is not None and self.cur.tid != 1:
            self.feed = False
            if self.region:
                self.other_insns += n
            return
        self.feed = True
        self.blk_addr = address
        h = self.blocks.get((address, size))
        if h is None:
            code = bytes(uc.mem_read(address, min(size, 256)))
            last = int.from_bytes(uc.mem_read(address + size - 4, 4), 'little') if size >= 4 else 0
            h = self.L.a57sim_block_h(address + self.delta, n, last, code, len(code))
            self.blocks[(address, size)] = h
        self.L.a57sim_block_exec_h(h)
        if self.region:
            self.main_insns += n
            if self.max_insns and self.main_insns >= self.max_insns and not self.stopped_by:
                self.stopped_by = 'max-insns'
                uc.emu_stop()

    def _on_mem(self, uc, access, address, size, value, _):
        if not self.feed:
            return
        pc = uc.reg_read(UC_ARM64_REG_PC)
        st = 1 if access == UC_MEM_WRITE else 0
        h = self.mems.get((pc, st))
        if h is None:
            h = self.L.a57sim_mem_h(pc + self.delta, st)
            self.mems[(pc, st)] = h
        self.L.a57sim_mem_access_h(h, address, size, (pc - self.blk_addr) >> 2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('nso')
    ap.add_argument('--elf', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--lib')
    ap.add_argument('--params', default=os.path.join(HERE, 'params.json'))
    ap.add_argument('--threads', choices=('main', 'all'), default='main')
    ap.add_argument('--start')
    ap.add_argument('--stop')
    ap.add_argument('--max-insns', type=int, default=0)
    ap.add_argument('--no-mem', action='store_true')
    ap.add_argument('--tag', default='all')
    ap.add_argument('--root')
    ap.add_argument('--file', action='append', default=[])
    ap.add_argument('--load-base', type=lambda v: int(v, 0))
    ap.add_argument('--skip-faulting-init', action='store_true')
    ap.add_argument('--budget', type=int, default=400_000_000)
    a = ap.parse_args(argv)
    L = load_lib(a.lib)
    with open(a.params) as f:
        for k, v in json.load(f)['sim'].items():
            if L.a57sim_set(k.encode(), float(v[0] if isinstance(v, list) else v)):
                raise SystemExit(f'uctrace: unknown simulator parameter {k}')
    L.a57sim_init()
    root = a.root or tempfile.mkdtemp(prefix='fnvnx-a57-')
    for spec in a.file:
        name, _, src = spec.partition('=')
        dst = os.path.join(root, name.lstrip('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    emu = Trace(a.nso, root, load_base=a.load_base)
    emu.budget = a.budget
    emu.load_symbols(a.elf)
    if a.skip_faulting_init:
        emu.install_guards()
    emu.trace_setup(a, L)
    t0 = time.time()
    try:
        rc = emu.run()
        why = 'guest returned %#x' % rc if rc is not None else 'guest returned'
    except SystemExit as e:
        why = str(e)
    except Exception as e:  # noqa: BLE001 - write what was counted so far
        why = 'host stand-in raised %r' % e
    if emu.stopped_by:
        why = emu.stopped_by
    if os.path.exists(a.out):
        os.remove(a.out)
    L.a57sim_dump(a.out.encode(), a.tag.encode())
    with open(a.out, 'a') as f:
        f.write(f'# uctrace: stopped: {why}\n# uctrace: main {emu.main_insns} other {emu.other_insns} '
                f'seconds {time.time() - t0:.0f}\n')
    print(f'uctrace: stopped: {why}; main {emu.main_insns} insns, other threads {emu.other_insns}, '
          f'{time.time() - t0:.0f} s')
    if not a.root:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
