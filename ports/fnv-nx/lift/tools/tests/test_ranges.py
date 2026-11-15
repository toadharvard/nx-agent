"""`.fnvl.sites` records (16 bytes: kind, x86 pc, host start, host end) on a compiled unit: each record is kind and pc plain, start and end R_AARCH64_P32_ABS32 in .text, and the range is exactly
the exclusive sequence (kind 1: ldaxr .. cbnz) or the one store / the one bfxil (kind 2). Compiled by a stage of the recipe (toolchain/tests/plugbuild.py), no direct compiler call.   test_ranges.py"""
import os, re, shutil, subprocess, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT); sys.path.insert(0, os.path.join(LIFT, 'tools')); sys.path.insert(0, os.path.join(LIFT, '..', 'toolchain', 'tests'))
import fnvlift as L
if len(sys.argv) != 2: sys.exit('usage: %s GEN   (GEN: the GEN output of the lift/rt root, a declared input of the test root)' % os.path.basename(__file__))
GEN = sys.argv[1]
import plugbuild as pb

SRC = '''
extern "C" void L_t(u32 ft0){ u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0,c0=0,c2=0,c3=0;
 { LK_BEGIN(32); lk_401000: ; LK32(eax)=LK32(eax)+ecx; if(!lk_commit(lk_,0x401000u)){ LK_RESTORE; goto lk_401000; } }
 FSW_ST16(ecx,FSWV(c->ftop),0x402000u);
 FSW_AX(FSWV(c->ftop),0x402002u);
 FSW_ST16(0x500002u,FSWV(c->ftop),0x402004u);
 FNSTENV(edx,1,FSWV(c->ftop),0x402006u); }
'''


def main():
    work = tempfile.mkdtemp(prefix='ranges-')
    src = os.path.join(work, 't.cc'); obj = os.path.join(work, 't.o')
    open(src, 'w').write(L.prelude(os.path.join(LIFT, 'rt'), GEN) + SRC)
    r = pb.compile(src, obj, [pb.build(n, work) for n in ('sdd', 'names', 'dispatch')])
    assert r.returncode == 0, r.stderr[-1500:]
    dis = subprocess.run(['aarch64-linux-gnu-objdump', '-d', obj], capture_output=True, text=True, check=True).stdout
    insn = {int(m.group(1), 16): m.group(2).split()[0] for m in re.finditer(r'^\s*([0-9a-f]+):\t[0-9a-f]{8} *\t(.*)$', dis, re.M)}
    out = {}
    for sec, kind in (('.fnvl.lk', 1), ('.fnvl.fsw', 2)):
        rel = subprocess.run(['aarch64-linux-gnu-readelf', '-r', obj], capture_output=True, text=True, check=True).stdout
        blk = re.search(r"Relocation section '\.rela%s'.*?(?=\n\n|\Z)" % re.escape(sec), rel, re.S)   # the last section of the listing has no blank line after it
        rows = [l.split() for l in blk.group(0).splitlines() if 'P32_ABS' in l] if blk else []
        words = {int(r[0], 16): int(r[-1], 16) for r in rows}
        data = subprocess.run(['aarch64-linux-gnu-objcopy', '-O', 'binary', '--only-section=' + sec, obj, '/dev/stdout'], capture_output=True, check=True).stdout
        assert len(data) % 12 == 0 and len(data) > 0, (sec, len(data))
        recs = []
        for o in range(0, len(data), 12):
            pc = int.from_bytes(data[o:o + 4], 'little')
            assert o not in words, 'the pc word is plain'
            assert o + 4 in words and o + 8 in words, 'start and end are relocated'
            recs.append((kind, pc, words[o + 4], words[o + 8]))
        out.setdefault('sites', []).extend(recs)
        for k, pc, s, e in recs:
            body = [insn[a] for a in range(s, e, 4)]
            if k == 1:
                assert body[0].startswith('ldaxr') and body[-1] == 'cbnz' and any(b.startswith('stlxr') for b in body) and not any(b in ('bl', 'blr') for b in body), (hex(pc), body)
            else:
                assert k == 2 and len(body) == 1 and body[0] in ('strh', 'str', 'bfxil'), (hex(pc), body)
    got = sorted((r[0], r[1]) for r in out['sites'])
    assert got == [(1, 0x401000), (2, 0x402000), (2, 0x402002), (2, 0x402004), (2, 0x402006)], got      # one record per instruction
    print('ranges_test ok: %d records' % len(got))
    shutil.rmtree(work, ignore_errors=True)


main()
