#!/usr/bin/env python3
"""Self-test of the Cortex-A57 model (tools/perf/a57model, docs/PERF.md 12).

    python3 -I tests/s15/test_a57model.py [--keep DIR]

1. dataflow: cost.dependent_loads finds pointer chases and memory-carried chains, and nothing else;
2. simulator (liba57sim.so through ctypes): an L1-sized loop does not miss, a 64 MB random walk misses L2, a stride
   is prefetched, a loop branch is predicted;
3. calibration: the kernels under qemu-system-aarch64 + a57trace.so against calib/truth.json; the check error
   (the METRIC) must stay at or under LIMIT_CHECK and the fit points at or under LIMIT_FIT.
Skips the parts whose tools are missing (gcc, llvm-mca, capstone; qemu-system-aarch64 and aarch64-linux-gnu-gcc
for 3). About a minute.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, '..', '..', 'tools', 'perf', 'a57model')
sys.path.insert(0, MODEL)
sys.path.insert(0, os.path.join(MODEL, '..'))
sys.path.insert(0, os.path.join(MODEL, '..', '..'))
import tables  # noqa: E402
LIMIT_CHECK = 6.0  # 4.8 % over 13 independent points after V1 P0-E1/E2  # % mean absolute error over the check points; lower it as the model improves
LIMIT_FIT = 2.0

fails = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        fails.append(what)


def asm_words(src):
    """assemble with llvm-mc -> bytes"""
    p = subprocess.run(['llvm-mc', '-triple=aarch64', '-show-encoding'], input=src, capture_output=True, text=True)
    out = b''
    import re
    for m in re.finditer(r'encoding: \[([^\]]+)\]', p.stdout):
        out += bytes(int(x, 16) for x in m.group(1).split(','))
    return out


def test_dataflow():
    import cost
    if cost.CS is None or not shutil.which('llvm-mc'):
        print('skip dataflow: capstone or llvm-mc missing')
        return
    code = asm_words('ldr x0, [x0]\nldr x1, [x2, #8]\nadd x3, x1, #1\nldr x4, [x3]\nldr q0, [x5], #16\n'
                     'str x6, [sp, #8]\nldr x6, [sp, #8]\nsubs x7, x7, #1\nb.ne #-32\n')
    il = cost.disasm(0x1000, code)
    dep, mc = cost.dependent_loads(il)
    check(0x1000 in dep, 'pointer chase ldr x0,[x0] is dependent')
    check(0x100c in dep, 'load through a register computed from a load is dependent')
    check(0x1004 not in dep and 0x1010 not in dep, 'streaming and independent loads are not dependent')
    check(0x1018 in mc, 'reload of a spilled loaded value is a memory chain')


def test_divides():
    import cost
    if cost.CS is None or not shutil.which('llvm-mca'):
        print('skip divides: capstone or llvm-mca missing')
        return
    n = 1000
    words = [0x1e211800] * 4 + [0xf1000421, 0x54ffff61]   # fdiv s0, s0, s1 x4; subs x1, x1, #1; b.ne
    blk = dict(pc=0x1000, n=len(words), count=n, mispred=0, taken=n, btb_l2=0, btb_miss=0, icl1=0, icl2=0, itlb=0,
               bytes=b''.join(w.to_bytes(4, 'little') for w in words))
    reg = {'R': dict(icount=0, dram_rd=0, dram_wr=0, l2fill=0, blocks=0, accesses=0), 'B': [blk], 'M': []}
    params = cost.load_params(os.path.join(MODEL, 'params.json'))
    _f, tot = cost.price(reg, params, 1.02, cost.MCA(None))
    per = tot['cycles'] / n / 4
    check(abs(per - 12) < 0.6, f'fdiv s chain priced mid-range: {per:.2f} cycles (guide 7-17)')
    check(abs(tot['div_band'] / n / 4 - 5) < 0.6, f"divide band +-{tot['div_band'] / n / 4:.2f} cycles (half of 7-17)")


def test_sim(out):
    if not shutil.which('gcc'):
        print('skip sim: gcc missing')
        return
    subprocess.run(['gcc', '-O2', '-fPIC', '-shared', '-o', os.path.join(out, 'liba57sim.so'),
                    os.path.join(MODEL, 'a57sim.c')], check=True)
    L = ctypes.CDLL(os.path.join(out, 'liba57sim.so'))
    L.a57sim_block_h.restype = ctypes.c_uint64
    L.a57sim_block_h.argtypes = [ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32]
    L.a57sim_mem_h.restype = ctypes.c_uint64
    L.a57sim_mem_h.argtypes = [ctypes.c_uint64, ctypes.c_int]
    L.a57sim_block_exec_h.argtypes = [ctypes.c_uint64]
    L.a57sim_mem_access_h.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32]
    L.a57sim_dump.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    L.a57sim_init()
    bne = 0x54ffffe1  # b.ne back
    blk = L.a57sim_block_h(0x10000, 4, bne, b'', 0)
    ld = L.a57sim_mem_h(0x10000, 0)
    ld2 = L.a57sim_mem_h(0x10004, 0)
    import random
    rnd = random.Random(1)
    # a 16 KB array (twice: warm, then counted), then a 64 MB random walk
    for rep in range(2):
        if rep == 1:
            L.a57sim_region(1)
        for i in range(0, 16384, 8):
            L.a57sim_block_exec_h(blk)
            L.a57sim_mem_access_h(ld, 0x100000 + i, 8, 0)
    for i in range(2048):
        L.a57sim_block_exec_h(blk)
        L.a57sim_mem_access_h(ld2, 0x8000000 + rnd.randrange(64 << 20) // 64 * 64, 8, 1)
    L.a57sim_region(0)
    st = os.path.join(out, 'sim.txt')
    L.a57sim_dump(st.encode(), b't')
    import cost
    r = cost.parse_stats(st)['t']
    m = {x['pc']: x for x in r['M']}
    b = r['B'][0]
    check(m[0x10000]['l1m'] + m[0x10000]['l2m'] == 0, 'L1-resident array: no L1D misses')
    check(m[0x10004]['l2m'] > 0.8 * m[0x10004]['count'], f"64 MB random: L2 misses {m[0x10004]['l2m']} of {m[0x10004]['count']}")
    check(b['mispred'] <= 2, f"loop branch predicted ({b['mispred']} mispredicts)")
    L.a57sim_init()  # frees every handle: take new ones
    blk = L.a57sim_block_h(0x10000, 4, bne, b'', 0)
    ld = L.a57sim_mem_h(0x10000, 0)
    L.a57sim_region(1)
    for i in range(0, 1 << 20, 64):
        L.a57sim_block_exec_h(blk)
        L.a57sim_mem_access_h(ld, 0x200000 + i, 8, 0)
    L.a57sim_region(0)
    os.remove(st)
    L.a57sim_dump(st.encode(), b't')
    r = cost.parse_stats(st)['t']
    m = r['M'][0]
    check(m['pf_hit'] > 0.9 * m['count'], f"stride stream prefetched ({m['pf_hit']} of {m['count']} hit prefetched lines)")


def test_funcmodel(out):
    import cost
    if cost.CS is None or not shutil.which('aarch64-linux-gnu-gcc') or not shutil.which('llvm-mca'):
        print('skip funcmodel: capstone, aarch64-linux-gnu-gcc or llvm-mca missing')
        return
    src = os.path.join(out, 'f.c')
    with open(src, 'w') as f:
        f.write('float dot3(const float *a, const float *b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }\n'
                'int find(const int *a, int n, int k) { for (int i = 0; i < n; i++) if (a[i] == k) return i; return -1; }\n'
                'int main(void) { return 0; }\n')
    elf = os.path.join(out, 'f.elf')
    subprocess.run(['aarch64-linux-gnu-gcc', '-O2', '-mcpu=cortex-a57', '-nostdlib', '-static', '-e', 'main',
                    '-o', elf, src], check=True)
    import funcmodel
    res = funcmodel.price_funcs(elf, ['dot3', 'find'], cost.MCA(None), cost.MCA(None, iterations=1))
    by = {r['func']: r for r in res}
    check(all(r['cpi'] and r['cpi_once'] for r in res), 'funcmodel prices both functions')
    check(all(r['cpi'] <= r['cpi_once'] for r in res), 'steady-state CPI <= one-call CPI')
    check(by['find']['blocks'] >= 3 and by['dot3']['blocks'] == 1, f"blocks: find {by['find']['blocks']}, dot3 {by['dot3']['blocks']}")


def test_mtmodel():
    try:
        import mtmodel
        import frame_budget
    except Exception as e:  # noqa: BLE001
        print(f'skip mtmodel: {e}')
        return
    with open(tables.path('budget_model.json')) as f:
        model = json.load(f)
    flat = mtmodel.run(model, None, ['placed', 'plan45'], [1020], 15.0, True)
    fb = frame_budget.build(model)
    fbs = {s['id']: s for s in fb['scenarios']}
    by = {s['id']: s for s in flat['scenarios']}
    p_mt = by['placed']['results']['1020']['mid']['frame_ms']
    p_fb = fbs['placed']['results']['1020']['frame_ms']['mid']
    check(abs(p_mt - p_fb) <= 0.02 * p_fb, f'flat, no hand-offs: mtmodel {p_mt} ms = frame_budget {p_fb} ms')
    q_mt = by['plan45']['results']['1020']['mid']['frame_ms']
    q_fb = fbs['plan45']['results']['1020']['frame_ms']['mid']
    check(q_mt >= q_fb, f'plan45: critical path {q_mt} ms >= water-fill {q_fb} ms')
    t = [mtmodel.Thread('main', [0], 0), mtmodel.Thread('w', [1, 2], 2)]
    t[0].ops = [('fork', 'k', 'w', 3000, 'x'), ('run', 1000, 'a'), ('join', 'k')]
    end, _b, cores, wait, _t = mtmodel.simulate(t, 1000, 10)
    check(abs(end - 13) <= 1 and abs(wait - 12) <= 1, f'fork/join: main ends at {end} us (13), waits {wait} us (12)')


def test_calibration(out):
    for t in ('qemu-system-aarch64', 'aarch64-linux-gnu-gcc', 'llvm-mca', 'pkg-config'):
        if not shutil.which(t):
            print(f'skip calibration: {t} missing')
            return
    rep = os.path.join(out, 'calib.json')
    p = subprocess.run([sys.executable, '-I', os.path.join(MODEL, 'calibrate.py'), '--out', out, '--json', rep],
                       capture_output=True, text=True, timeout=1200)
    print(p.stdout[-2500:])
    if p.returncode or not os.path.exists(rep):
        if 'a57trace.so not built' in p.stderr or 'qemu-plugin.h' in p.stderr:
            print('skip calibration: no qemu-plugin.h')
            return
        check(False, 'calibrate.py ran: ' + p.stderr[-500:])
        return
    with open(rep) as f:
        r = json.load(f)
    check(r['check_mape_pct'] <= LIMIT_CHECK, f"check error {r['check_mape_pct']} % <= {LIMIT_CHECK} %")
    check(r['fit_mape_pct'] <= LIMIT_FIT, f"fit error {r['fit_mape_pct']} % <= {LIMIT_FIT} %")
    check(r['check_mape_pct'] < r['flat_check_mape_pct'], 'better than the flat IPC model')


def main():
    keep = sys.argv[sys.argv.index('--keep') + 1] if '--keep' in sys.argv else None
    out = keep or tempfile.mkdtemp(prefix='a57model-')
    os.makedirs(out, exist_ok=True)
    try:
        test_dataflow()
        test_sim(out)
        test_divides()
        test_funcmodel(out)
        test_mtmodel()
        test_calibration(out)
    finally:
        if not keep:
            shutil.rmtree(out, ignore_errors=True)
    print(f'{len(fails)} failed')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
