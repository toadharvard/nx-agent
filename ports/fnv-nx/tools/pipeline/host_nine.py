#!/usr/bin/env python3
"""host_nine.py - LP64 host module with Gallium Nine (NINE=1; pipeline adapter, stream S9-build).

    DEVKITPRO=... tools/pipeline/host_nine.py <render/nine/build.sh work dir> <gen_proxy.py out dir> <out dir>

S6c's render/nine has the Mesa build (build.sh), the adapter (host/nine_adapter.c) and the proxy generator, but no
host-module build yet (render/nine/README.md, "Host build integration"). Until it has one, this compiles S5's
toolchain/host/host.c with the same flags as tests/s5/hello/build.sh, plus render/nine/host/*.c and the generated
nine_proxy_host.c against the Mesa tree, and links them with the Nine/nouveau static libraries and libnx. The
Nine entry points are forced into the link (--undefined), since host.c does not call them yet; what stays
undefined (IDirect3D9, ID3DPresent, the manual proxy bodies: S6c work in progress) is reported, not fatal.

Output: <out>/host.elf, <out>/nine_link.json {objects, failed, undefined}, <out>/nine_link.log.
"""
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
HOST_ARCH = ['-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-mtp=soft', '-fPIE']
ENTRY_POINTS = ['nx_nine_create_adapter', 'nine_dispatch']
LIBS = ['nine_st', 'nouveauwinsys', 'nouveau', 'gallium', 'galliumvl_stub', 'softpipe', 'nir', 'compiler',
        'mesa_format', 'mesa_util', 'drm_nouveau']


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    work, proxy, out = (os.path.abspath(a) for a in argv)
    dkp = os.environ['DEVKITPRO']
    cc = os.path.join(dkp, 'devkitA64', 'bin', 'aarch64-none-elf-gcc')
    libnx = os.path.join(dkp, 'libnx')
    mesa = os.path.join(work, 'src', 'mesa')
    os.makedirs(os.path.join(out, 'nine-obj'), exist_ok=True)
    logf = os.path.join(out, 'nine_link.log')
    open(logf, 'w').close()

    def run(a):
        p = subprocess.run(a, capture_output=True, text=True, errors='replace')
        with open(logf, 'a') as f:
            f.write('$ %s\n%s%s[exit %d]\n' % (' '.join(map(shlex.quote, a)), p.stdout, p.stderr, p.returncode))
        return p

    inc = ['-I' + os.path.join(ROOT, 'toolchain', 'include'), '-I' + os.path.join(libnx, 'include'),
           '-I' + os.path.join(ROOT, 'render', 'nine'), '-I' + os.path.join(work, 'out', 'include')]
    for sub in ('include', 'include/D3D9', 'src', 'src/gallium/include', 'src/gallium/auxiliary',
                'src/gallium/state_trackers/nine', 'src/gallium/winsys/nouveau/switch', 'src/gallium/drivers/nouveau',
                'src/util', 'src/mapi', 'build-nine/src', 'build-nine/src/util', 'build-nine/src/gallium/auxiliary'):
        if os.path.isdir(os.path.join(mesa, sub)):
            inc.append('-I' + os.path.join(mesa, sub))
    cflags = HOST_ARCH + ['-O2', '-g', '-ffunction-sections', '-fdata-sections', '-D__SWITCH__',
                          '-DHAVE_TIMESPEC_GET', '-w']
    srcs = [os.path.join(ROOT, 'toolchain', 'host', 'host.c')]
    hd = os.path.join(ROOT, 'render', 'nine', 'host')
    srcs += sorted(os.path.join(hd, n) for n in os.listdir(hd) if n.endswith('.c')) if os.path.isdir(hd) else []
    if os.path.isfile(os.path.join(proxy, 'nine_proxy_host.c')):
        srcs.append(os.path.join(proxy, 'nine_proxy_host.c'))
    objs, failed = [], []
    for s in srcs:
        o = os.path.join(out, 'nine-obj', os.path.basename(s) + '.o')
        extra = [] if s.endswith('host.c') and 'toolchain' in s else inc
        p = run([cc] + cflags + extra + ['-I' + os.path.join(ROOT, 'toolchain', 'include'),
                                          '-I' + os.path.join(libnx, 'include'), '-c', s, '-o', o])
        if p.returncode:
            err = next((l for l in (p.stderr or '').splitlines() if 'error' in l), 'exit %d' % p.returncode)
            failed.append([os.path.relpath(s, ROOT) if s.startswith(ROOT) else os.path.basename(s), err[:200]])
        else:
            objs.append(o)
    host_o = os.path.join(out, 'nine-obj', 'host.c.o')
    res = {'objects': [os.path.basename(o) for o in objs], 'failed': failed, 'undefined': []}
    if host_o not in objs:
        json.dump(res, open(os.path.join(out, 'nine_link.json'), 'w'), indent=1)
        print('host.c does not compile')
        return 1
    libdir = os.path.join(work, 'out', 'lib')
    libs = ['-l' + n for n in LIBS if os.path.isfile(os.path.join(libdir, 'lib%s.a' % n))]
    elf = os.path.join(out, 'host.elf')
    p = run([cc, '-specs=' + os.path.join(libnx, 'switch.specs')] + HOST_ARCH + objs +
            ['-Wl,--undefined=' + e for e in ENTRY_POINTS] +
            ['-L' + libdir, '-Wl,--start-group'] + libs + ['-Wl,--end-group', '-L' + os.path.join(libnx, 'lib'),
                                                           '-lnx', '-lstdc++', '-lm', '-Wl,--warn-unresolved-symbols',
                                                           '-Wl,-Map,' + os.path.join(out, 'host_nine.map'), '-o', elf])
    res['undefined'] = sorted(set(re.findall(r"undefined reference to `([^']+)'", p.stdout + p.stderr)))
    res['link_rc'] = p.returncode
    json.dump(res, open(os.path.join(out, 'nine_link.json'), 'w'), indent=1)
    print('host + nine: %d objects (%d failed), %d undefined symbols, link exit %d' % (
        len(objs), len(failed), len(res['undefined']), p.returncode))
    return 0 if p.returncode == 0 and os.path.isfile(elf) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
