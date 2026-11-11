#!/usr/bin/env python3
"""synth_lift - a synthetic stand-in for the output of lift/tools/liftall.py --finish, at full scale, and the run of the link
stage on it (PLAN_FIRST_ELF A7).  TEST CODE: nothing here is a product path.

    synth_lift.py --universe U --out DIR [--bytes-per-entry-scale 9] [--shards 64] [--import-keys 8] [--red-cases N] [--l3a DIR]

Writes under DIR what liftall writes: `lift_K_of_N.a` (one `void L_<8 hex>(void)` per universe row in input section `.text.fnvl`),
`disp.o` (lift/bench/phf.py table_asm of L3a, imported from the scratch copy --l3a), `rt.a` (L3a's own rt.c, x87.c, cpumodel.c,
seh.c, enter.c compiled as liftall.py compiles them), `entries.tsv`; plus test-only objects: `crt0.o` (`__fnvnx_guest_start`, a table
of absolute pointers to bodies that gives the PIE its R_AARCH64_P32_RELATIVE relocations) and `fixture.o` (the symbols the real
platform runtime provides to rt.a, and the import-key symbols), and `keys.tsv` for the import keys.  The body of an entry is about
scale x the extent of its row in bytes of real instructions, makes a direct `bl` to the next entry (address order), calls
`fnvl_dispatch(key)()` and, for the first and the last body of the link layout, makes a direct `bl` to the other one: the
farthest pair of the layout, so the linker has to reach across the whole `.text.fnvl` (veneers when it exceeds the 128 MiB of a bl).
The L3a scratch copy is made with `git show origin/claude/fnv-l3a-lift:...` when it is missing.

Then runs link/ld.py (guest.ld), gate_disp.py and gate_pcrel.py and prints one line per gate, the link wall time and the linker's
peak RSS.  --red-cases N repeats the run on the first N rows with one defect each: an entry dropped from its archive, a slot of
the table retargeted, a body that takes the address of an absolute symbol PC-relatively.  Everything derived from the universe
stays under DIR (container only, never the repository).
"""
import argparse
import collections
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.join(HERE, '..')
sys.path.insert(0, LINK)
import gate_disp

X = 'aarch64-linux-gnu-'
CC, CXX, AR, NM, READELF, LD = X + 'gcc', X + 'g++', X + 'ar', X + 'nm', X + 'readelf', '/usr/bin/' + X + 'ld'
L3A_REF = 'origin/claude/fnv-l3a-lift'
L3A_FILES = ['lift/bench/phf.py', 'lift/rt/fnvl.h', 'lift/rt/fnvl_x87.h', 'lift/rt/x87.c', 'lift/rt/x87.h', 'lift/rt/seh.c', 'lift/rt/cpumodel.c',
             'lift/rt/rt.c', 'lift/rt/enter.c']
CHUNK = 100                     # bodies per object
BODY_FLAGS = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-w', '-c'] + \
    ['-ffixed-x%d' % r for r in range(19, 28)]
PLAIN = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c']
# lift/test/instest.py FLAGS without -DFNVL_FLAGS_IO -DFNVL_STUBX (the flags of liftall.flags())
LIFT_CXX = ('-mabi=ilp32 -std=gnu++17 -c -O2 -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv '
            '-fno-delete-null-pointer-checks -fcheck-new -fno-sized-deallocation -fno-lifetime-dse -ffp-contract=off -fPIC -fpermissive '
            '-march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector -fno-stack-clash-protection -mno-outline-atomics -U_FORTIFY_SOURCE '
            '-ffreestanding -fno-exceptions -fno-asynchronous-unwind-tables -fno-math-errno -w -ffixed-x19 -ffixed-x20 -ffixed-x21 -ffixed-x22 '
            '-ffixed-x23 -ffixed-x24 -ffixed-x25 -ffixed-x26 -ffixed-x27').split()
PAD = '.rept %d\\n\\tadd w9, w9, #1\\n\\teor w10, w10, w9\\n\\tlsl w11, w10, #3\\n\\torr w9, w9, w11\\n\\t.endr'     # 16 bytes each
FIXTURE = """/* TEST FIXTURE (synth_lift.py): stands in for what the packaged platform runtime provides to L3a's rt.a. Not part of L3a's interface. */
unsigned long long fnvl_host_ticks_ns(void) { return 0; }
void fnvl_os_unhandled(unsigned int record, unsigned int context) { (void)record; (void)context; }
void *memset(void *p, int c, __SIZE_TYPE__ n) { char *q = p; while (n--) *q++ = (char)c; return p; }
"""


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError('%s failed (%d): %s' % (cmd[0], r.returncode, (r.stderr or r.stdout)[-1500:]))
    return r.stdout


def l3a_copy(d):
    """Extract the L3a files this runner needs into d (flat layout phf.py, rt/...), unless they are there already."""
    repo = sh(['git', '-C', HERE, 'rev-parse', '--show-toplevel']).strip()
    for f in L3A_FILES:
        dst = os.path.join(d, 'rt', os.path.basename(f)) if f.startswith('lift/rt/') else os.path.join(d, os.path.basename(f))
        if not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'w', newline='\n') as fh:
                fh.write(sh(['git', '-C', repo, 'show', '%s:ports/fnv-nx/%s' % (L3A_REF, f)]))


def load_universe(path):
    """[(address, extent)] sorted by address."""
    out = []
    with open(path) as fh:
        for ln in fh:
            if ln.strip() and ln[0] != '#':
                p = ln.split('\t')
                out.append((int(p[0], 16), int(p[2])))
    return sorted(out)


def body(a, nxt, key, nbytes, far=None, absref=False):
    s = ['__attribute__((section(".text.fnvl"))) void L_%08x(void)\n{\n' % a,
         '    { extern void L_%08x(void); L_%08x(); }\n' % (nxt, nxt)]
    if far is not None:
        s.append('    { extern void L_%08x(void); L_%08x(); }\n' % (far, far))
    if absref:
        s.append('    { extern char fnv_abs[] __attribute__((visibility("hidden"))); *(volatile unsigned *)fnv_abs = 1; }\n')
    s.append('    ((void (*)(void))fnvl_dispatch(0x%08x))();\n' % key)
    s.append('    __asm__ volatile("%s" ::: "w9", "w10", "w11", "cc");\n}\n' % (PAD % max(1, nbytes // 16)))
    return ''.join(s)


def compile_chunks(ents, order, shards, scale, objdir, srcdir, drop, absref, jobs):
    """Compile every body into objects objdir/lift_<shard>_<chunk>.o; returns {shard: [object paths]}."""
    n = len(ents)
    pos = {a: i for i, (a, _) in enumerate(ents)}
    first, last = order[0], order[-1]
    todo = []
    for k in range(shards):
        mine = [ents[i] for i in range(k, n, shards)]
        for c in range(0, len(mine), CHUNK):
            txt = ['extern void (*fnvl_dispatch(unsigned))(void);\n']
            for a, ext in mine[c:c + CHUNK]:
                if a == drop:
                    continue
                i = pos[a]
                far = last if a == first else first if a == last else None
                txt.append(body(a, ents[(i + 1) % n][0], ents[(i + 2) % n][0], scale * ext, far, a == absref))
            todo.append((k, c // CHUNK, ''.join(txt)))

    def one(t):
        k, c, text = t
        src = os.path.join(srcdir, 'lift_%03d_%03d.c' % (k, c))
        obj = os.path.join(objdir, 'lift_%03d_%03d.o' % (k, c))
        with open(src, 'w', newline='\n') as fh:
            fh.write(text)
        sh([CC] + BODY_FLAGS + [src, '-o', obj])
        os.remove(src)
        return k, obj
    out = collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for k, obj in ex.map(one, todo):
            out[k].append(obj)
    return out


def build(ents, out, scale, shards, import_keys=8, l3a=None, drop=None, retarget=None, absref=None, jobs=3):
    """Build the synthetic liftall output in out (in/ holds the link inputs, obj/ every object, entries.tsv, keys.tsv).
    ents [(address, extent)] sorted; drop: address whose body is left out of its archive (entry and table slot stay);
    retarget: (address, other address): the table slot of the first names the body of the second; absref: address whose body
    takes the address of the absolute symbol fnv_abs PC-relatively.  Returns the far pair."""
    sys.path.insert(0, l3a)
    import phf
    for d in ('in', 'obj', 'src'):
        shutil.rmtree(os.path.join(out, d), ignore_errors=True)
        os.makedirs(os.path.join(out, d))
    inn, objd, srcd = (os.path.join(out, d) for d in ('in', 'obj', 'src'))
    n = len(ents)
    order = [ents[i][0] for k in range(shards) for i in range(k, n, shards)]
    chunks = compile_chunks(ents, order, shards, scale, objd, srcd, drop, absref, jobs)
    for k in range(shards):
        if chunks.get(k):
            sh([AR, 'Dcr', os.path.join(inn, 'lift_%d_of_%d.a' % (k, shards))] + chunks[k])
    syms = {a: 'L_%08x' % a for a, _ in ents}
    with open(os.path.join(out, 'entries.tsv'), 'w', newline='\n') as fh:
        fh.write(''.join('%08x\t%s\n' % (a, syms[a]) for a in sorted(syms)))
    keys = {0xf0000000 + 4 * i: 'fnvl_import_%d' % i for i in range(import_keys)}
    with open(os.path.join(out, 'keys.tsv'), 'w', newline='\n') as fh:
        fh.write(''.join('%08x\t%s\n' % (k, keys[k]) for k in sorted(keys)))
    tab = dict(syms)
    tab.update(keys)
    if retarget:
        tab[retarget[0]] = syms[retarget[1]]
    h = phf.build(list(tab))
    with open(os.path.join(out, 'disp.S'), 'w', newline='\n') as fh:
        fh.write(phf.table_asm(tab, h))
    sh([CC, '-mabi=ilp32', '-fPIC', '-c', os.path.join(out, 'disp.S'), '-o', os.path.join(inn, 'disp.o')])
    # the lifter runtime, as liftall.finish() builds it
    df = ['-DFNVL_PHF_REL', '-DFNVL_K1=%du' % h['k1'], '-DFNVL_K2=%du' % h['k2'], '-DFNVL_NBITS=%d' % h['nbits'], '-DFNVL_TBITS=%d' % h['tbits']]
    rt = os.path.join(l3a, 'rt')
    rto = []
    for cmd, f in (([CC] + PLAIN + df, 'rt.c'), ([CC] + PLAIN + ['-I', rt], 'x87.c'), ([CC] + PLAIN, 'cpumodel.c'),
                   ([CXX] + LIFT_CXX + ['-I', rt, '-x', 'c++'], 'seh.c'), ([CXX] + LIFT_CXX + ['-I', rt, '-x', 'c++'], 'enter.c')):
        o = os.path.join(objd, 'rt_%s.o' % f[:-2])
        sh(cmd + [os.path.join(rt, f), '-o', o])
        rto.append(o)
    sh([AR, 'Dcr', os.path.join(inn, 'rt.a')] + rto)
    # test-only objects
    fx = os.path.join(out, 'src', 'fixture.c')
    with open(fx, 'w', newline='\n') as fh:
        fh.write(FIXTURE + ''.join('void %s(void) {}\n' % keys[k] for k in sorted(keys)))
    sh([CC] + PLAIN + ['-fno-builtin', '-fno-tree-loop-distribute-patterns', fx, '-o', os.path.join(objd, 'fixture.o')])
    ptrs = [ents[i][0] for i in range(0, n, max(1, n // 64))]
    with open(os.path.join(out, 'src', 'crt0.S'), 'w', newline='\n') as fh:
        fh.write('.section .crt0,"ax"\n.globl __fnvnx_guest_start\n__fnvnx_guest_start:\n bl fnvnx_lift_enter\n ret\n'
                 '.data\n.balign 4\n.globl fixture_entry_ptrs\nfixture_entry_ptrs:\n' + ''.join(' .word L_%08x\n' % a for a in ptrs))
    sh([CC, '-mabi=ilp32', '-fPIC', '-c', os.path.join(out, 'src', 'crt0.S'), '-o', os.path.join(objd, 'crt0.o')])
    for o in ('crt0.o', 'fixture.o'):
        shutil.copy(os.path.join(objd, o), os.path.join(inn, o))
    shutil.rmtree(srcd)
    shutil.copy(os.path.join(inn, 'disp.o'), os.path.join(objd, 'disp.o'))
    return order[0], order[-1]


WRAP = """#!/usr/bin/env python3
# test-only: runs the linker and records its wall time and its peak RSS (kB) next to this file
import resource, subprocess, sys, time
t = time.monotonic()
r = subprocess.run(['%s'] + sys.argv[1:])
open(__file__ + '.stat', 'w').write('%%.1f %%d\\n' %% (time.monotonic() - t, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))
sys.exit(r.returncode)
"""


def link_and_gate(ents, out, far, absref=None, scale=None):
    """Run ld.py, gate_disp.py and gate_pcrel.py on out/in. Returns {gate: (exit status, stdout line)} and measurements."""
    go = os.path.join(out, 'gates')
    shutil.rmtree(os.path.join(out, 'ld'), ignore_errors=True)
    os.makedirs(go, exist_ok=True)
    wrap = os.path.join(out, 'ldwrap.py')
    with open(wrap, 'w', newline='\n') as fh:
        fh.write(WRAP % LD)
    os.chmod(wrap, 0o755)
    env = dict(os.environ, GATE_OUT=go)
    res, meas = {}, {}
    cmd = [sys.executable, '-I', os.path.join(LINK, 'ld.py'), '--ld', wrap, '--ld-arg', '-m', '--ld-arg', 'aarch64linux32']
    if absref is not None:
        cmd += ['--ld-arg', '--defsym=fnv_abs=0x400000']
    cmd += ['--script', os.path.join(LINK, 'guest.ld'), '--in', os.path.join(out, 'in'), '--out', os.path.join(out, 'ld')]
    t = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    meas['ld.py wall s'] = round(time.monotonic() - t, 1)
    res['ld'] = (r.returncode, (r.stdout.strip() or r.stderr.strip()[-300:]))
    try:
        with open(wrap + '.stat') as fh:
            w, rss = fh.read().split()
        meas['linker wall s'], meas['linker peak RSS MB'] = float(w), round(int(rss) / 1024)
    except OSError:
        pass
    elf = os.path.join(out, 'ld', 'guest.elf')
    if os.path.exists(elf):
        r = subprocess.run([sys.executable, '-I', os.path.join(LINK, 'gate_disp.py'), '--elf', elf, '--universe', os.path.join(out, 'universe.tsv'),
                            '--entries', os.path.join(out, 'entries.tsv'), '--keys', os.path.join(out, 'keys.tsv')], capture_output=True, text=True, env=env)
        res['disp'] = (r.returncode, r.stdout.strip() or r.stderr.strip()[-300:])
        r = subprocess.run([sys.executable, '-I', os.path.join(LINK, '..', 'contract', 'gate_pcrel.py'), '--elf', elf, '--objects', os.path.join(out, 'obj'),
                            '--work', os.path.join(out, 'pcrel'), '--readelf', READELF, '--nm', NM], capture_output=True, text=True, env=env)
        res['pcrel'] = (r.returncode, r.stdout.strip().replace('\n', '; ') or r.stderr.strip()[-300:])
        rel = collections.Counter(l.split()[2] for l in sh([READELF, '-rW', elf]).splitlines() if len(l.split()) >= 3 and l.split()[2].startswith('R_AARCH64'))
        meas['dynamic relocations'] = ', '.join('%s %d' % kv for kv in sorted(rel.items())) or 'none'
        for l in sh([READELF, '-SW', elf]).splitlines():
            if '.text.fnvl' in l:
                meas['.text.fnvl bytes'] = int(l.split('.text.fnvl')[1].split()[3], 16)
        syms = gate_disp.read_elf(elf)[0]
        meas['veneers'] = sum(1 for s in syms if 'veneer' in s)
        meas['far pair distance bytes'] = abs(next(iter(syms['L_%08x' % far[0]])) - next(iter(syms['L_%08x' % far[1]])))
    return res, meas


def run(universe, out, scale, shards, import_keys, l3a, rows=None, **defect):
    os.makedirs(out, exist_ok=True)
    ents = load_universe(universe)
    if rows:
        ents = ents[:rows]
    with open(os.path.join(out, 'universe.tsv'), 'w', newline='\n') as fh:
        fh.write(''.join('%08x\tdecomp\t%d\t-\n' % e for e in ents))
    t = time.monotonic()
    far = build(ents, out, scale, shards, import_keys, l3a, **defect)
    built = round(time.monotonic() - t, 1)
    res, meas = link_and_gate(ents, out, far, defect.get('absref'))
    meas['build s'] = built
    return ents, res, meas


def report(title, res, meas):
    print('== %s' % title)
    for g in ('ld', 'disp', 'pcrel'):
        if g in res:
            print('%s gate: exit %d: %s' % (g, res[g][0], res[g][1]))
    for k in sorted(meas):
        print('%s: %s' % (k, meas[k]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--universe', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--bytes-per-entry-scale', type=int, default=9)
    ap.add_argument('--shards', type=int, default=64)
    ap.add_argument('--import-keys', type=int, default=8)
    ap.add_argument('--red-cases', type=int, default=0, help='also run the three defect cases on the first N rows')
    ap.add_argument('--l3a', default='/home/user/l1work/l3a')
    a = ap.parse_args()
    l3a_copy(a.l3a)
    ents, res, meas = run(a.universe, a.out, a.bytes_per_entry_scale, a.shards, a.import_keys, a.l3a)
    report('full scale: %d entries, scale %d, %d shards' % (len(ents), a.bytes_per_entry_scale, a.shards), res, meas)
    if a.red_cases:
        n = a.red_cases
        for name, defect in (('entry dropped from its archive', dict(drop=0)), ('table slot retargeted', dict(retarget=(0, 1))),
                             ('absolute symbol taken PC-relatively', dict(absref=0))):
            small = load_universe(a.universe)[:n]
            d = {k: (small[v][0] if k in ('drop', 'absref') else (small[v[0]][0], small[v[1]][0])) for k, v in defect.items()}
            _, res, meas = run(a.universe, os.path.join(a.out, 'red_%s' % name.split()[0]), 4, 4, a.import_keys, a.l3a, rows=n, **d)
            report('red case: %s (%s)' % (name, ', '.join('%s=%s' % kv for kv in sorted(d.items()))), res, meas)


if __name__ == '__main__':
    main()
