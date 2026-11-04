#!/usr/bin/env python3
"""test_nx1_checks.py - V1 (D14) against NX1's device-readiness checks 2-8 and the native host default
(c203030, 78d75b2, 785d700, 3db7366, e6c895f, e89ee89, 023421d, d9462c7, bc92651, 6c432f0, 8a726fd, 57907a2).

    python3 -I tests/v1/test_nx1_checks.py     -> one line per case, exit 1 when a case shows a defect

Each check gets a synthetic input it must reject and one it must accept. ELFs are assembled here from a few
instructions (aarch64-linux-gnu-as/ld); the source-reading checks (6, 8) run on a copy of the files they read in a
temp tree with one thing changed. Synthetic inputs only: no game data, no decomp text.

Accept cases (a defect when the check refuses a good input or a fact of the claim does not hold):
  H1  the pipeline's host stage defaults to HOST_BACKEND=native (empty or unset), package/build.sh to HOST=native
  G2  check 6 refuses a plain host ELF (no dkDeviceCreate) and a native one that links consoleInit, passes native
  M1  check 2 passes a first-cell boot with a 100 MiB peak, refuses 1,400 MiB, a main-menu boot and a 1 GiB space
  S4  check 3 passes a clean host (svc 0x01, mrs cntpct_el0, dc zva, "vi:m" as a libnx alternative); refuses
      mrs cntvct_el0, svc 0x7f, "ldr:pm", and a guest with any svc
  T1  check 4: placement.h over cpu 0..255 / -1 / roles x seq x core-3 off keeps every thread on cores 0-2, every
      thread_set_cpu spec too; the encoded NPDM's main stack (META +0x1C) is 0x400000
  F1  check 5 passes a package tree with fnv.nro, fnv-exefs.nsp, guest.nso; refuses one holding Data/
  C1  check 8 passes the checklist; refuses a copy without the hold-R step
  L1  57907a2: a check script that prints a Latin-1 byte and exits 1 is a fail in run.py, exit 0 a pass
Refutation cases (a defect when the check passes what the console would not run, or the claim does not hold):
  G1  785d700 "the package has a GPU backend at all": check 6 passes a native host ELF without any input about the
      guest, and the full build's default guest is linked D3D9=null without render/native/overrides.tsv, so the
      default package that check 6 passes draws nothing (it waits for no guest evidence)
  G3  78d75b2 "pools evaluated from the defines": a 600 MiB block written (600u * 1024 * 1024) is read as 600 bytes
      (the define regex takes the first number), and pools.fit passes 249 + 600 MiB in a 512 MiB host heap
  S1  e6c895f: `dc cvap` is on the allowlist; it is ARMv8.2 (FEAT_DPB), UNDEFINED on the Cortex-A57 (ARMv8.0)
  S2  e6c895f "nothing that traps at EL0": ARMv8.1+ instructions (LSE ldadd/casal, RCpc ldapr, FP16 fadd h) pass
      the scan; on the A57 each is UNDEFINED (the scan looks only at the 0xD4/0xD5/0xD6 classes)
  S3  e89ee89 "fails only on names nobody reviewed": a service name outside the scan's corpus ("spsm", "psc:m",
      system services nobody reviewed) passes services_reviewed: unknown names are dropped, not failed
  S5  e6c895f "system-only ones such as pm:shell ... fail": a host whose rodata names "pm:shell" passes; the
      service regex takes at most 4 characters after the colon ("shell" has 5), so the name is never found
  P1  6c432f0 "orderly exit on HOME close": nothing in the console code calls appletLockExit, so AM never sends
      ExitRequest on "Close software": the process is terminated (libnx applet.h, appletLockExit: "instead of being
      force-terminated") and the pump's cleanup/exit(0) path, which host_device_test drives with a stub, never runs
"""
import contextlib
import importlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
NX1 = os.path.join(PORT, 'tests', 'nx1')
sys.path.insert(0, NX1)
sys.path.insert(0, os.path.join(PORT, 'toolchain', 'tools'))
AS, LD = 'aarch64-linux-gnu-as', 'aarch64-linux-gnu-ld'
BAD = 0


def case(cid, ok, why):
    global BAD
    print('%s %s' % (cid, 'ok' if ok else 'DEFECT: ' + why))
    BAD += not ok


def elf(t, name, text, rodata=''):
    """a static AArch64 ELF: text in an R-X segment, rodata (asciz strings) in a separate R segment"""
    s = os.path.join(t, name + '.s')
    open(s, 'w').write('.arch armv8.2-a+lse+rcpc+fp16\n.text\n.globl _start\n_start:\n' + text +
                       '\n\tret\n.section .rodata\n' + rodata + '\n')
    o, e = s[:-2] + '.o', s[:-2] + '.elf'
    subprocess.run([AS, s, '-o', o], check=True)
    subprocess.run([LD, '-static', '-z', 'separate-code', '-e', '_start', o, '-o', e], check=True)
    return e


def mirror(t, rels):
    """a copy of the port's files the checks read; returns the new root"""
    root = os.path.join(t, 'port')
    for r in rels:
        src, dst = os.path.join(PORT, r), os.path.join(root, r)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    return root


def run_py(root, rel, *args):
    r = subprocess.run([sys.executable, '-I', os.path.join(root, rel)] + list(args), capture_output=True, text=True,
                       errors='replace', timeout=300)
    return r.returncode, r.stdout + r.stderr


def fails(out):
    return [l.split(':')[0][5:] for l in out.splitlines() if l.startswith('FAIL ')]


def main():
    if not (shutil.which(AS) and shutil.which(LD)):
        print('skip: aarch64-linux-gnu binutils missing')
        return 0
    t = tempfile.mkdtemp(prefix='v1-nx1c-')
    try:
        body(t)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('%d defects' % BAD)
    return 1 if BAD else 0


def body(t):
    import check_gpu
    import check_mem
    import check_svc

    # ---- H1: the host backend default
    bp = open(os.path.join(PORT, 'tools', 'pipeline', 'build.py')).read()
    cfg = re.search(r"\('HOST_BACKEND',\s*'(\w+)'", bp)
    reads = "cfg = {k: os.environ.get(k, d) or d for k, d, _ in CONFIG}" in bp
    dispatch = re.search(r"elif b\.cfg\['HOST_BACKEND'\] == 'native':\s*\n\s*b\.say\('host: native renderer", bp)
    pk = open(os.path.join(PORT, 'toolchain', 'package', 'build.sh')).read()
    case('H1', cfg and cfg.group(1) == 'native' and reads and dispatch and 'case "${HOST:-native}" in' in pk,
         'HOST_BACKEND default %s, env read %s, native dispatch %s' % (cfg and cfg.group(1), reads, bool(dispatch)))

    # ---- G1 / G2: check 6's package items
    nat = elf(t, 'native', '\tbl dkDeviceCreate\n\tsvc #0x1\ndkDeviceCreate:\n\tnop')
    natcon = elf(t, 'natcon', '\tbl dkDeviceCreate\n\tbl consoleInit\ndkDeviceCreate:\n\tnop\nconsoleInit:\n\tnop')
    plain = elf(t, 'plain', '\tbl consoleInit\nconsoleInit:\n\tnop')
    def pkg(e):
        with contextlib.redirect_stdout(io.StringIO()):
            return {r[0]: r[1] for r in check_gpu.check(e).rows if r[0].startswith('package.')}
    pn, pc, pp = pkg(nat), pkg(natcon), pkg(plain)
    case('G2', all(pn.values()) and not pc.get('package.no_console', True) and not pp['package.native'],
         'native %s, native+console %s, plain %s' % (pn, pc, pp))
    guest_null = re.search(r"'D3D9':\s*'external' if b\.cfg\['NINE'\] == '1' else 'null'", bp) is not None
    ob = open(os.path.join(PORT, 'tools', 'link', 'overrides', 'build.sh')).read()
    native_ovr = 'render/native/overrides.tsv' in bp or 'render/native' in ob
    case('G1', not (all(pn.values()) and guest_null and not native_ovr),
         'check 6 passes the package on a native host.elf alone (package.native/no_console %s); the default full '
         'build links the guest with D3D9=null and without render/native/overrides.tsv, so nothing calls nxgpu '
         '(ext module 10) and that package draws a black screen' % pn)

    # ---- G3: a large pool written as a product
    root = mirror(t, ['tests/nx1', 'render/native/host', 'toolchain/include', 'toolchain/tools'])
    g = os.path.join(root, 'render/native/host/nxgpu.c')
    src = open(g).read()
    m = re.search(r'\n(\s*)(g\.tsMem = host_block\(0x1000,[^;]*;)', src)
    src = src.replace(m.group(0), '\n%s%s\n%sg.bigMem = host_block(BIG_POOL, DkMemBlockFlags_GpuCached);\n'
                      '%sif (!g.bigMem) return NULL;' % (m.group(1), m.group(2), m.group(1), m.group(1)), 1)
    src = '#define BIG_POOL (600u * 1024 * 1024)\n' + src
    open(g, 'w').write(src)
    rc, out = run_py(root, 'tests/nx1/check_gpu.py')
    info = next((l for l in out.splitlines() if l.startswith('info native pools')), '')
    case('G3', 'pools.fit' in fails(out),
         'a 600 MiB block in nxgpu_init passes pools.fit: %s' % info[:120])

    # ---- M1: check 2 on synthetic guest ELF and boot summaries
    guest = elf(t, 'guest', '\tnop')
    bj = os.path.join(t, 'boot.json')

    def mem(peak, ms):
        json.dump({'peak_heap_bytes': peak, 'milestone': ms}, open(bj, 'w'))
        with contextlib.redirect_stdout(io.StringIO()):
            return check_mem.check(guest, bj, 'all').passed
    good, big, early = mem(100 << 20, 'first-cell'), mem(1400 << 20, 'first-cell'), mem(4 << 20, 'main-menu')
    orig = check_mem.HEAP_REGION
    check_mem.HEAP_REGION = {k: 1024 << 20 for k in orig}      # a 1 GiB heap region (32Bit space)
    small = mem(100 << 20, 'first-cell')
    check_mem.HEAP_REGION = orig
    case('M1', good and not big and not early and not small,
         'pass 100 MiB %s, 1400 MiB %s, main-menu %s, 1 GiB space %s' % (good, big, early, small))

    # ---- S1-S4: check 3 on assembled ELFs
    def svc(path, role='host'):
        c = check_svc.Checks()
        with contextlib.redirect_stdout(io.StringIO()):
            check_svc.scan(path, role, c)
        return {r[0]: r[1] for r in c.rows}
    clean = elf(t, 'clean', '\tsvc #0x1\n\tmrs x0, cntpct_el0\n\tdc zva, x0\n\tdc civac, x0',
                '.asciz "sm:"\n.asciz "vi:m"\n.asciz "hid"\n.asciz "fsp-srv"')
    cr = svc(clean)
    rej = {}
    for k, (txt, ro) in {'cntvct': ('\tsvc #0x1\n\tmrs x0, cntvct_el0', ''),
                         'svc7f': ('\tsvc #0x1\n\tsvc #0x7f', ''),
                         'ldr_pm': ('\tsvc #0x1', '.asciz "ldr:pm"')}.items():
        rej[k] = all(svc(elf(t, k, txt, ro)).values())
    gsv = all(svc(elf(t, 'gsvc', '\tsvc #0x1'), 'guest').values())
    case('S4', all(cr.values()) and not any(rej.values()) and not gsv,
         'clean %s; passed although bad: %s; guest svc passes %s' % ({k: v for k, v in cr.items() if not v},
                                                                       [k for k, v in rej.items() if v], gsv))
    cvap = svc(elf(t, 'cvap', '\tsvc #0x1\n\tdc cvap, x0'))
    case('S1', not cvap['host.insn'], '`dc cvap, x0` (ARMv8.2 FEAT_DPB, UNDEFINED on the Cortex-A57) passes host.insn')
    v81 = {}
    for k, txt in {'ldadd': 'ldadd w0, w1, [x2]', 'casal': 'casal w0, w1, [x2]', 'ldapr': 'ldapr w0, [x1]',
                   'fadd_h': 'fadd h0, h1, h2', 'swp': 'swp x0, x1, [x2]'}.items():
        v81[k] = svc(elf(t, 'v81_' + k, '\tsvc #0x1\n\t' + txt))['host.insn']
    case('S2', not any(v81.values()), 'ARMv8.1+ instructions pass the scan (UNDEFINED on the A57, a crash on the '
         'console): %s' % [k for k, v in v81.items() if v])
    unk = svc(elf(t, 'unk', '\tsvc #0x1', '.asciz "spsm"\n.asciz "psc:m"\n.asciz "pm:info"'))
    pms = svc(elf(t, 'pmshell', '\tsvc #0x1', '.asciz "pm:shell"'))
    case('S5', not pms['host.services_reviewed'], '"pm:shell" (named by e6c895f as a system-only name that fails) '
         'passes: SERVICE_RX allows at most 4 characters after the colon, so the scan never sees it')
    case('S3', not unk['host.services_reviewed'], 'unreviewed service names "spsm", "psc:m" (system services, not in '
         'APP_SERVICES or the libnx alternatives) pass services_reviewed: SERVICE_RX hits outside KNOWN_SERVICES are '
         'dropped')

    # ---- T1: placement and the NPDM's main stack
    cc = shutil.which('gcc') or shutil.which('cc')
    probe = os.path.join(t, 'probe.c')
    open(probe, 'w').write(r'''#include <stdio.h>
#include "placement.h"
int main(void) {
    int bad = 0;
    long n = 0;
    for (long cpu = -3; cpu < 0x200; cpu++)
        for (unsigned seq = 0; seq < 7; seq++)
            for (long pr = -20; pr <= 20; pr += 5) {
                int ideal = -9, prio = 0; uint32_t mask = 0;
                fnvnx_place_ex((uint64_t)cpu, seq, pr, 0, &ideal, &mask, &prio);
                n++;
                if (ideal < 0 || ideal > 2 || !mask || (mask & ~7u) || !(mask >> ideal & 1) || prio < 0x1C || prio > 0x3B)
                    if (bad++ < 5) printf("FAIL place cpu %#lx seq %u: ideal %d mask %#x prio %#x\n", cpu, seq, ideal, mask, prio);
            }
    for (int ci = 0; ci < 3; ci++)
        for (uint32_t cm = 1; cm < 8; cm++) {
            if (!(cm >> ci & 1)) continue;
            for (uint64_t sp = 0; sp < 70; sp++) {
                uint64_t specs[3] = {sp, 1ull << 63 | (sp & 0xF) << 8 | (sp >> 4), 1ull << 63 | (sp << 8) | FNVNX_CPU_KEEP};
                for (int k = 0; k < 3; k++) {
                    int ideal; uint32_t mask;
                    fnvnx_cpu_spec(specs[k], ci, cm, &ideal, &mask);
                    n++;
                    if (ideal < 0 || ideal > 2 || !mask || (mask & ~7u) || !(mask >> ideal & 1))
                        if (bad++ < 10) printf("FAIL spec %#llx cur %d/%#x: ideal %d mask %#x\n", (unsigned long long)specs[k], ci, cm, ideal, mask);
                }
            }
        }
    printf("%ld placements, %d bad\n", n, bad);
    return bad != 0;
}
''')
    exe = os.path.join(t, 'probe')
    r = subprocess.run([cc, '-O1', '-Wall', '-I' + os.path.join(PORT, 'toolchain', 'host'),
                        '-I' + os.path.join(PORT, 'toolchain', 'include'), probe, '-o', exe], capture_output=True, text=True)
    pr = subprocess.run([exe], capture_output=True, text=True) if r.returncode == 0 else None
    npdm = os.path.join(t, 'main.npdm')
    subprocess.run([sys.executable, '-I', os.path.join(PORT, 'toolchain', 'tools', 'npdmtool.py'),
                    os.path.join(PORT, 'toolchain', 'npdm', 'fnvnx-app.json'), npdm], check=True, capture_output=True)
    d = open(npdm, 'rb').read()
    stack = struct.unpack_from('<I', d, 0x1C)[0] if d[:4] == b'META' else None
    case('T1', pr is not None and pr.returncode == 0 and stack == 0x400000,
         'placement: %s; META main stack %s' % ((pr.stdout.strip().splitlines() or ['?'])[-1] if pr else r.stderr[:200],
                                                 stack and hex(stack)))

    # ---- F1: check 5's package items
    sd = os.path.join(t, 'sd')
    base = os.path.join(sd, 'switch', 'fnv')
    os.makedirs(base)
    for n in ('fnv.nro', 'fnv-exefs.nsp', 'guest.nso'):
        open(os.path.join(base, n), 'wb').write(b'x')
    rc1, o1 = run_py(PORT, 'tests/nx1/check_fs.py', '--sd', sd, '--no-s4')
    os.makedirs(os.path.join(base, 'Data'))
    rc2, o2 = run_py(PORT, 'tests/nx1/check_fs.py', '--sd', sd, '--no-s4')
    case('F1', rc1 == 0 and rc2 != 0 and 'pkg.no_game_files' in fails(o2), 'clean tree rc %d (%s); with Data/ rc %d'
         % (rc1, fails(o1), rc2))

    # ---- C1: check 8
    rc1, o1 = run_py(PORT, 'tests/nx1/check_checklist.py')
    root = mirror(t + '/c', ['tests/nx1', 'docs/NRO_CHECKLIST.md', 'toolchain/include', 'toolchain/nro/launcher.c',
                             'toolchain/tools'])
    p = os.path.join(root, 'docs', 'NRO_CHECKLIST.md')
    open(p, 'w').write(open(p).read().replace('hold R', 'press R'))
    rc2, o2 = run_py(root, 'tests/nx1/check_checklist.py')
    case('C1', rc1 == 0 and rc2 != 0 and 'steps.takeover' in fails(o2), 'checklist rc %d; without hold R rc %d' % (rc1, rc2))

    # ---- L1: lenient decoding keeps a tool's failure a failure
    sys.path.insert(0, NX1)
    run = importlib.import_module('run')
    fake = os.path.join(t, 'fake')
    os.makedirs(fake)
    for name, code in (('fail_l1.py', 1), ('pass_l1.py', 0)):
        open(os.path.join(fake, name), 'w').write(
            'import sys\nsys.stdout.buffer.write(b"FAIL x: caf\\xe9\\n" if %d else b"caf\\xe9 ok\\n")\nsys.exit(%d)\n'
            % (code, code))
    old = run.HERE
    run.HERE = fake
    try:
        sf = run.script('fail_l1.py')(None)
        sp = run.script('pass_l1.py')(None)
    finally:
        run.HERE = old
    case('L1', sf[0] == 'fail' and sp[0] == 'pass', 'exit 1 -> %s, exit 0 -> %s' % (sf, sp))

    # ---- P1: HOME close
    srcs = ''
    for d in ('toolchain/host', 'toolchain/nro', 'render/native/host', 'startup/host'):
        for dp, _, fns in os.walk(os.path.join(PORT, d)):
            for f in fns:
                if f.endswith(('.c', '.inc', '.h')):
                    srcs += open(os.path.join(dp, f), errors='replace').read()
    relies = 'appletMainLoop()' in srcs and 'cleanup_for_exit("the system asked the application to exit")' in srcs
    hdr = os.path.join(PORT, 'ext', 'libnx', 'nx', 'include', 'switch', 'services', 'applet.h')
    doc = open(hdr).read() if os.path.isfile(hdr) else ''
    forced = 'instead of being force-terminated' in doc or not doc
    case('P1', not relies or 'appletLockExit' in srcs or not forced,
         'the host handles "Close software" in its pump (appletMainLoop false -> cleanup_for_exit, exit(0)) but never '
         'calls appletLockExit; without it AM terminates the process on HOME close (libnx applet.h: "%s"), so no '
         'cleanup runs' % ('instead of being force-terminated' if doc else 'libnx checkout missing'))


if __name__ == '__main__':
    sys.exit(main())
