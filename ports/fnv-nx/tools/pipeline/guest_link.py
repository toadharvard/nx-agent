#!/usr/bin/env python3
"""guest_link.py - full ILP32 guest link of the game (pipeline adapter, stream S9-build).

    DEVKITPRO=... [FNVNX_TC=...] tools/pipeline/guest_link.py <S2 out dir> <S2 fixed dir> <out dir>

Same interface as S5's toolchain/game/build.sh (which build.py prefers as soon as it exists): S2's rewritten
objects + aliases.ld (data-image addresses, COMDAT aliases) + bridges + addrmap (zz_*.o in the fixed dir), the
guest runtime of toolchain/guest/, platform/ (win32, crt, audio, input) and the S3 loader (loader/src) compiled for
ILP32 with fnvnx-a64, linked through toolchain/guest/guest.specs (guest.ld, fixed address 0x10000000).

* Sources of platform/, loader/ and the runtime that do not compile are listed (summary.json) and left out.
* The whole image is linked (--no-gc-sections): there is no boot code here, so garbage collection from `main`
  would drop most of the game and hide what is undefined.
* Pass 1 collects every undefined symbol (undefined.txt); pass 2 binds each one to a trap stub (traps.S) that
  reports it at run time (guest_stub.c), so the link always produces fnvnx.elf and guest.nso.

Env: DEVKITPRO (required), FNVNX_TC (default $DEVKITPRO/devkitA64), SSE2NEON, JOBS, D3D9 (null|external: no D3D9
device is linked either way here; external adds EXTRA_OBJS, e.g. render/nine's guest proxy), EXTRA_OBJS.
Output: fnvnx.elf, fnvnx.map, guest.nso, undefined.txt, traps.S, link1.log, link2.log, summary.json.
"""
import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
TCDIR = os.path.join(ROOT, 'toolchain')
ARCH = ['-mabi=ilp32', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-pie', '-ftls-model=local-exec',
        '-fsigned-char']   # D10 (J-0034): MSVC-like plain char, as toolchain/game/build.sh
COMMON = ARCH + ['-O2', '-g', '-ffunction-sections', '-fdata-sections', '-frounding-math', '-fshort-wchar', '-w']
P = os.path.join(ROOT, 'platform')
PINC = ['-I' + os.path.join(P, 'include'), '-iquote', os.path.join(ROOT, 'shim', 'include'),
        '-I' + os.path.join(ROOT, 'shim', 'include'), '-I' + os.path.join(P, 'crt'), '-I' + os.path.join(TCDIR, 'include')]


def log(*a):
    print(*a, flush=True)


def run(argv, logf=None, cwd=None):
    p = subprocess.run(argv, capture_output=True, text=True, errors='replace', cwd=cwd)
    if logf:
        with open(logf, 'a') as f:
            f.write('$ ' + ' '.join(map(shlex.quote, argv)) + '\n' + p.stdout + p.stderr + '[exit %d]\n' % p.returncode)
    return p


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    s2out, fixed, out = (os.path.abspath(a) for a in argv)
    dkp = os.environ.get('DEVKITPRO')
    if not dkp:
        print('set DEVKITPRO')
        return 2
    tc = os.environ.get('FNVNX_TC') or os.path.realpath(os.path.join(dkp, 'devkitA64'))
    gcc = os.path.join(tc, 'bin', 'aarch64-none-elf-gcc')
    gxx = os.path.join(tc, 'bin', 'aarch64-none-elf-g++')
    jobs = int(os.environ.get('JOBS') or os.cpu_count() or 4)
    os.environ['FNVNX_TOOLCHAIN'] = TCDIR       # guest.specs finds guest.ld through it
    objd, rtd = os.path.join(out, 'obj'), os.path.join(out, 'rt')
    for d in (objd, rtd):
        os.makedirs(d, exist_ok=True)
        for n in os.listdir(d):
            os.remove(os.path.join(d, n))
    clog = os.path.join(out, 'compile.log')
    open(clog, 'w').close()

    # ---- what to compile: (source, object, flags)
    jobsl = []
    g = os.path.join(TCDIR, 'guest')
    jobsl.append((os.path.join(g, 'crt0.s'), os.path.join(rtd, 'crt0.o'), [gcc] + ARCH))
    rtflags = [gcc] + ARCH + ['-O2', '-g', '-Wall', '-Wextra', '-ffunction-sections', '-fdata-sections',
                              '-I' + os.path.join(TCDIR, 'include')]
    for n in ('runtime.c', 'threads.c', 'fs.c'):            # threads.c / fs.c: S5, when present
        if os.path.isfile(os.path.join(g, n)):
            jobsl.append((os.path.join(g, n), os.path.join(rtd, n[:-2] + '.o'), rtflags))
    srcs = []
    for sub, exts in (('win32', ('.cpp',)), ('crt', ('.c', '.cpp')), ('audio', ('.cpp', '.c')),
                      ('input', ('.cpp', '.c'))):
        d = os.path.join(P, sub)
        if os.path.isdir(d):
            srcs += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(exts)]
    srcs += [os.path.join(P, 'clock', n) for n in ('clock_rt.cpp', 'clock_real.cpp')]   # the clock interface (the product's real clock)
    for n in ('os_guest.c', 'platform_glue.cpp'):            # S5's guest OS backend, when present
        if os.path.isfile(os.path.join(P, 'guest', n)):
            srcs.append(os.path.join(P, 'guest', n))
    ld = os.path.join(ROOT, 'loader', 'src')
    if os.path.isdir(ld):
        srcs += [os.path.join(ld, n) for n in sorted(os.listdir(ld)) if n.endswith('.c')]
    srcs.append(os.path.join(HERE, 'guest_stub.c'))
    for s in srcs:
        rel = os.path.relpath(s, ROOT)
        o = os.path.join(objd, rel.replace('/', '_') + '.o')
        if s.endswith('.c'):
            fl = [gcc] + COMMON + ['-std=gnu11'] + PINC
        else:
            fl = [gxx] + COMMON + ['-std=gnu++17', '-DSTDMETHODCALLTYPE='] + PINC
        if rel.startswith('loader/'):
            fl = fl + ['-I' + os.path.join(ROOT, 'loader', 'include'), '-I' + ld]
        jobsl.append((s, o, fl))

    def comp(j):
        s, o, fl = j
        p = run(fl + ['-c', s, '-o', o])
        return s, o, p.returncode, (p.stdout + p.stderr)

    failed, built = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        for s, o, rc, text in ex.map(comp, jobsl):
            rel = os.path.relpath(s, ROOT)
            with open(clog, 'a') as f:
                f.write('== %s [exit %d]\n%s' % (rel, rc, text))
            if rc:
                lines = text.strip().splitlines()
                first = next((l for l in lines if 'error' in l), lines[-1] if lines else '')
                failed.append((rel, re.sub(r'^\S+:\d+:\d+:\s*', '', first)[:200]))
            else:
                built.append(o)
    crt0 = os.path.join(rtd, 'crt0.o')
    if not os.path.isfile(crt0) or not os.path.isfile(os.path.join(rtd, 'runtime.o')):
        log('guest runtime does not compile: %s' % failed)
        return 1
    plat = sorted(o for o in built if o.startswith(objd))
    rt = sorted(o for o in built if o.startswith(rtd) and o != crt0)
    game = sorted(os.path.join(fixed, n) for n in os.listdir(fixed) if n.endswith('.o'))
    rsp = os.path.join(out, 'game_objects.rsp')
    extra = shlex.split(os.environ.get('EXTRA_OBJS', ''))
    aliases = os.path.join(s2out, 'aliases.ld')
    elf = os.path.join(out, 'fnvnx.elf')
    excluded = []        # (object, reason): objects the linker refuses as input ("error adding symbols")

    nm = os.path.join(tc, 'bin', 'aarch64-none-elf-nm')

    def references(o, sym):
        p = run([nm, '-u', o])
        return any(l.split()[-1] == sym for l in p.stdout.splitlines() if l.strip())

    def culprits(text):
        """Input objects ld refuses: 'error adding symbols' (whole object) and relocation errors against a
        *defined* symbol (TLS-model reference to an absolute image address, misaligned cross-unit variable),
        mapped from the source name ld prints to the object that references the symbol."""
        found = {}
        for o, why in re.findall(r'(\S+\.o): error adding symbols: (.*)', text):
            ctx = [l.split(': ', 1)[-1] for l in text.splitlines()
                   if os.path.basename(o) in l and 'error adding symbols' not in l]
            found.setdefault(o, (ctx[0] if ctx else why).replace(ROOT + '/', ''))
        for src, rel_, sym, where in re.findall(r"([^/\s:]+):\([^)]*\): relocation truncated to fit: "
                                               r"(\S+) against symbol `([^']+)' defined in (\S+)", text):
            base = src if src.endswith('.o') else src + '.o'
            cand = [o for o in game + plat if os.path.basename(o) == base or os.path.basename(o).endswith('_' + base)]
            if len(cand) > 1:
                cand = [o for o in cand if references(o, sym)]
            for o in cand[:1]:
                found.setdefault(o, '%s against `%s` (%s)' % (rel_, sym, 'absolute image address: TLS-model '
                                                                 'reference' if where == '*ABS*' and 'TLS' in rel_
                                                                 else 'defined in ' + os.path.basename(where)))
        return found

    def link(extra_objs, logname, more=()):
        """Link; input objects ld refuses are dropped (listed in summary.json) and the link retried."""
        while True:
            with open(rsp, 'w') as f:
                f.write(''.join('"%s"\n' % o.replace('\\', '\\\\').replace('"', '\\"') for o in game))
            argv = [gxx] + ARCH + ['-specs=' + os.path.join(g, 'guest.specs'), crt0, '@' + rsp] + plat + rt + \
                extra + list(extra_objs) + [aliases, '-lm', '-Wl,--no-gc-sections', '-Wl,--no-demangle',
                                            '-Wl,-Map,' + os.path.join(out, 'fnvnx.map'), '-o', elf] + list(more)
            lf = os.path.join(out, logname)
            open(lf, 'w').close()
            p = run(argv, lf)
            text = p.stdout + p.stderr
            bad = {o: w for o, w in culprits(text).items() if o in game or o in plat} if p.returncode else {}
            if not bad or len(excluded) >= 200:
                return p.returncode, text
            for o, why in sorted(bad.items()):
                excluded.append((os.path.relpath(o, ROOT) if o.startswith(ROOT) else o, why[:300]))
                log('  ld refuses %s (%s): left out' % (os.path.basename(o), why[:160]))
                (game if o in game else plat).remove(o)

    log('guest link pass 1: %d game objects, %d platform/loader objects (%d failed), runtime %d' %
        (len(game), len(plat), len(failed), len(rt) + 1))
    rc, text = link([], 'link1.log', ['-Wl,--warn-unresolved-symbols'])
    undef = set(re.findall(r"undefined reference to `([^']+)'", text))
    undef |= set(re.findall(r"against undefined symbol `([^']+)'", text))
    multi = sorted(set(re.findall(r"multiple definition of `([^']+)'", text)))
    names = sorted(undef)
    traps = os.path.join(out, 'traps.S')
    passes = 1
    while True:
        with open(os.path.join(out, 'undefined.txt'), 'w', errors='surrogateescape') as f:
            f.write(''.join(n + '\n' for n in names))
        lines = ['// GENERATED by tools/pipeline/guest_link.py: one trap stub per undefined symbol', '    .text']
        for i, n in enumerate(names):
            q = '"' + n.replace('\\', '\\\\').replace('"', '\\"') + '"'
            lines += ['    .section .text.fnvnx_pipeline_trap.%d,"ax",%%progbits' % i, '    .p2align 2',
                      '    .globl %s' % q, '    .type %s, %%function' % q, '%s:' % q,
                      '    adrp x0, .Ltn%d' % i, '    add x0, x0, :lo12:.Ltn%d' % i, '    mov x1, x30',
                      '    b __fnvnx_pipeline_trap',
                      '    .section .rodata.fnvnx_pipeline_trapnames,"aMS",%progbits,1', '.Ltn%d:' % i,
                      '    .asciz %s' % q]
        open(traps, 'w', errors='surrogateescape').write('\n'.join(lines) + '\n')
        p = run([gcc] + ARCH + ['-c', traps, '-o', os.path.join(out, 'traps.o')], clog)
        if p.returncode:
            log('traps.S does not assemble: ' + (p.stderr.strip().splitlines() or [''])[0])
            return 1
        passes += 1
        more = ['-Wl,--allow-multiple-definition'] if multi else []
        rc, text = link([os.path.join(out, 'traps.o')], 'link2.log', more)
        new = set(re.findall(r"undefined reference to `([^']+)'", text)) - set(names)
        if rc == 0 or not new or passes > 8:
            break
        names = sorted(set(names) | new)
    summary = {'method': 'pipeline adapter (tools/pipeline/guest_link.py)', 'game_objects': len(game),
               'platform_units': len(plat), 'platform_failed': failed, 'runtime_objects': len(rt) + 1,
               'undefined': len(names), 'multidef': multi[:200], 'multidef_count': len(multi), 'link_passes': passes,
               'link_rc': rc, 'gc_sections': False, 'd3d9': os.environ.get('D3D9', 'null'), 'extra_objs': extra,
               'link_excluded': excluded}
    if rc:
        summary['link_error'] = [l for l in text.splitlines() if 'error' in l.lower()][:20]
        json.dump(summary, open(os.path.join(out, 'summary.json'), 'w'), indent=1)
        log('guest link pass %d failed:\n  %s' % (passes, '\n  '.join(summary['link_error'][:8])))
        return 1
    nso = os.path.join(out, 'guest.nso')
    p = run([sys.executable, '-I', os.path.join(TCDIR, 'tools', 'elf2nso.py'), elf, nso], clog)
    summary['elf2nso'] = (p.stdout + p.stderr).strip().splitlines()[-1:] if (p.stdout + p.stderr).strip() else []
    json.dump(summary, open(os.path.join(out, 'summary.json'), 'w'), indent=1)
    if p.returncode:
        log('elf2nso failed: ' + p.stderr.strip()[:400])
        return 1
    log('undefined symbols: %d (bound to trap stubs); multiple definitions: %d' % (len(names), len(multi)))
    log('linked %s -> %s' % (elf, nso))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
