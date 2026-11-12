#!/usr/bin/env python3
"""Synthetic check of gate_reach.py: hand-assembled PE32 images (no game data) and small AArch64 ILP32 objects."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import argflow
import imagerec
import gate_reach
import gpa_names

BASE, TEXT, RD, DATA = 0x400000, 0x1000, 0x2000, 0x3000
IMPORTS = ['LoadLibraryA', 'GetProcAddress', 'CreateFileMappingA', 'CreateFileA']
LL, GPA, CFM, CFA = [BASE + RD + 4 * i for i in range(4)]
S_K32, S_CFM, S_FILE, S_FOO = BASE + RD + 0x200, BASE + RD + 0x210, BASE + RD + 0x230, BASE + RD + 0x240
G_VAR, S_WR = BASE + DATA + 0x10, BASE + DATA
BRIDGE = ('dll\tname\tordinal\tconv\tret\targs\nkernel32.dll\tLoadLibraryA\t\tstdcall\ti\ti\nkernel32.dll\tGetProcAddress\t\tstdcall\ti\tii\n'
          'kernel32.dll\tCreateFileMappingA\t\tstdcall\ti\tiiiiii\nkernel32.dll\tCreateFileA\t\tstdcall\ti\tiiiiiii\n')


class Asm:
    def __init__(self):
        self.b, self.rel, self.funcs = bytearray(), [], {}

    def va(self):
        return BASE + TEXT + len(self.b)

    def raw(self, *bs):
        self.b += bytes(bs)

    def imm(self, op, v, reloc=True):
        self.b += bytes(op)
        if reloc:
            self.rel.append(TEXT + len(self.b))
        self.b += struct.pack('<I', v)

    def func(self, name):
        self.funcs[name] = [self.va(), None]

    def end(self, name):
        self.funcs[name][1] = self.va()
        self.raw(0xcc)

    def push_s(self, v):      self.imm([0x68], v)                    # push relocated address
    def push_i(self, v):      self.imm([0x68], v, False)             # push imm32, not relocated
    def push_b(self, v):      self.raw(0x6a, v & 0xff)               # push imm8 (sign-extended)
    def call(self, slot):     self.imm([0xff, 0x15], slot)
    def ret(self):            self.raw(0xc3)



def reloc_size(b):
    """The exact size of a base relocation table that is followed by zero padding."""
    pos = 0
    while pos + 8 <= len(b) and struct.unpack_from('<I', b, pos + 4)[0] >= 8:
        pos += struct.unpack_from('<I', b, pos + 4)[0]
    return pos

def pe_of(fill):
    a = Asm()
    fill(a)
    text = bytes(a.b).ljust(0x1000, b'\xcc')
    rd = bytearray(0x1000)
    for i, n in enumerate(IMPORTS):
        struct.pack_into('<I', rd, 4 * i, RD + 0x80 + 0x20 * i)
        rd[0x82 + 0x20 * i:0x82 + 0x20 * i + len(n) + 1] = n.encode() + b'\0'
    struct.pack_into('<IIIII', rd, 0x40, RD, 0, 0, RD + 0x70, RD)
    rd[0x70:0x70 + 13] = b'kernel32.dll\0'
    rd[0x200:0x20d] = b'kernel32.dll\0'
    rd[0x210:0x223] = b'CreateFileMappingA\0'
    rd[0x230:0x236] = b'a.txt\0'
    rd[0x240:0x244] = b'Foo\0'
    data = bytearray(0x1000)
    data[0:4] = b'Baz\0'
    blk0 = b''.join(struct.pack('<H', 0x3000 | (r - TEXT)) for r in a.rel)
    reloc = (struct.pack('<II', TEXT, 8 + len(blk0)) + blk0).ljust(0x200, b'\0')
    secs = [(b'.text', TEXT, text, 0x60000020), (b'.rdata', RD, bytes(rd), 0x40000040), (b'.data', DATA, bytes(data), 0xc0000040),
            (b'.reloc', 0x4000, reloc, 0x42000040)]
    hdr = bytearray(0x200)
    hdr[0:2] = b'MZ'
    struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x14c, len(secs), 0, 0, 0, 224, 0x2102)
    o = 0x58
    struct.pack_into('<H', hdr, o, 0x10b)
    struct.pack_into('<I', hdr, o + 16, TEXT)
    struct.pack_into('<I', hdr, o + 28, BASE)
    struct.pack_into('<II', hdr, o + 32, 0x1000, 0x200)
    struct.pack_into('<II', hdr, o + 56, 0x5000, 0x200)
    struct.pack_into('<I', hdr, o + 92, 16)
    struct.pack_into('<II', hdr, o + 96 + 8, RD + 0x40, 40)
    struct.pack_into('<II', hdr, o + 96 + 40, 0x4000, reloc_size(reloc))
    sh = o + 224
    out, fo = bytearray(hdr), 0x200
    for i, (nm, rva, body, ch) in enumerate(secs):
        struct.pack_into('<8sIIIIIIHHI', hdr, sh + 40 * i, nm, len(body), rva, len(body), fo, 0, 0, 0, 0, ch)
        out += body
        fo += len(body)
    out[0:0x200] = hdr
    return bytes(out), a


def cfm(a, hfile, prot):
    """CreateFileMappingA(hFile, 0, prot, 0, 0, 0): hfile(a) pushes argument 0 last."""
    a.push_b(0); a.push_b(0); a.push_b(0); a.push_b(prot); a.push_b(0)
    hfile(a)
    a.call(CFM)


def cfa(a, access, share):
    a.push_b(0); a.push_i(0x80); a.push_b(3); a.push_b(0); a.push_b(share); a.push_i(access); a.push_s(S_FILE); a.call(CFA)


def gpa_const(a, name_va):
    a.push_s(S_K32); a.call(LL); a.push_s(name_va); a.raw(0x50); a.call(GPA)


def image(fill, name):
    data, a = pe_of(fill)
    ranges = {s: [(s, e)] for s, e in a.funcs.values()}
    rtxt = ''.join('%x\t%x-%x\n' % (s, s, e) for s, (s, e) in sorted((s, (s, e)) for s, e in a.funcs.values()))
    img = argflow.Image(imagerec.ImageRec(data, name, ranges))
    img.import_stack = gpa_names.import_stacks(BRIDGE)
    return img, dict(name=name, pe=gate_reach.sha(data), ranges=gate_reach.sha(rtxt))


def gpa_rows(imgs, skip=()):
    res = []
    for nm, img in sorted(imgs.items()):
        for ref in sorted(r for r in img.relocs if img.word(r) == GPA):
            if (nm, ref) not in skip:
                res.append(((nm, ref, 'GetProcAddress', 'dynamic'), gpa_names.derive_site(img, ref)))
    return gate_reach.read_gpa(gpa_names.tsv(res))


def reqs(*rows):
    head = '\t'.join(gate_reach.COLUMNS) + '\n'
    return gate_reach.parse_requests(head + ''.join('\t'.join(r) + '\n' for r in rows))


R_H = ('H', 'kernel32', 'CreateFileMappingA', '0', 'imm_set', '0xFFFFFFFF', 'n')
R_P = ('P', 'kernel32', 'CreateFileMappingA', '2', 'imm_mask', '0x00000044', 'n')
R_W = ('W', 'kernel32', 'CreateFileMappingA', '0', 'result_of',
       'callee=kernel32!CreateFileA|kernel32!CreateFileW; arg1 & 0x50000006 == 0; arg2 & 0x00000002 == 0', 'n')
R_F = ('F', 'kernel32', 'CreateFileA', '1', 'imm_mask', '0x40000000', 'n')
R_W2 = ('W', 'kernel32', 'CreateFileMappingA', '0', 'handle_origin', 'callee=kernel32!CreateFileA; arg2 & 2 == 0', 'n')

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


def go(rs, fills, skip=()):
    imgs, meta = {}, []
    for nm, fill in sorted(fills.items()):
        imgs[nm], m = image(fill, nm)
        meta.append(m)
    facts, certs, g = gate_reach.run(rs, imgs, meta, gpa_rows(imgs, skip))
    return facts, certs, g


def reasons(facts):
    return {f.req['request']: sorted({r[0] for r in f.reasons}) for f in facts}


def sites(facts, request):
    return sorted(r[2] for f in facts if f.req['request'] == request for r in f.reasons)


# ---- imm_set / imm_mask
def ok_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')
    a.func('g1'); gpa_const(a, S_FOO); a.ret(); a.end('g1')


facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=ok_img))
check('certified imm', (reasons(facts), len(certs), g), ({'H': [], 'P': [], 'F': []}, 3, []))


def bad_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0xff), 4); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_H, R_P), dict(a=bad_img))
check('forbidden', (reasons(facts), len(certs)), ({'H': ['forbidden-immediate'], 'P': ['forbidden-bits']}, 0))
call_va = [c for c in argflow.imports_called(image(bad_img, 'a')[0], 'CreateFileMappingA')[0]][0]
check('site named', sites(facts, 'H'), ['0x%x' % call_va])


def nonconst_img(a):
    a.func('f1'); cfm(a, lambda a: a.imm([0xff, 0x35], G_VAR), 2); a.ret(); a.end('f1')   # push [global]


facts, certs, g = go(reqs(R_H), dict(a=nonconst_img))
check('non-constant', reasons(facts), {'H': ['arg-open']})


def ret_img(a):
    a.func('f1'); cfa(a, 0x80000000, 1); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_H), dict(a=ret_img))
check('call result is not an immediate', reasons(facts), {'H': ['not-immediate']})

# ---- GetProcAddress
def gpa_hit_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')
    a.func('g1'); gpa_const(a, S_CFM); a.ret(); a.end('g1')


facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=gpa_hit_img))
check('gpa pair', (reasons(facts), len(certs)), ({'H': ['gpa-pair'], 'P': ['gpa-pair'], 'F': []}, 1))


def gpa_open_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')
    a.func('g1'); a.push_s(S_K32); a.call(LL); a.push_s(S_WR); a.raw(0x50); a.call(GPA); a.ret(); a.end('g1')   # name in a writable section


facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=gpa_open_img))
check('gpa open', (reasons(facts), len(certs), len(g)), ({'H': ['gpa-open'], 'P': ['gpa-open'], 'F': ['gpa-open']}, 0, 1))
res_ids = gate_reach.uncertified_ids(facts)
check('ids format', res_ids[0].split(':')[:2], ['1', 'gpa-open'])
check('ids tail', [x.split(':')[-2] for x in res_ids], ['a'] * 3)
facts, certs, g = go(reqs(R_H), dict(a=ok_img), skip={('a', r) for r in image(ok_img, 'a')[0].relocs if image(ok_img, 'a')[0].word(r) == GPA})
check('gpa uncovered', reasons(facts), {'H': ['gpa-uncovered']})

# ---- result_of
def res_ok(a):
    a.func('f1'); cfa(a, 0x80000000, 1); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_ok))
check('result_of proven', (reasons(facts), len(certs)), ({'W': []}, 1))
facts, certs, g = go(reqs(R_W2), dict(a=res_ok))
check('handle_origin spelling', (reasons(facts), len(certs)), ({'W': []}, 1))


def res_bad(a):
    a.func('f1'); cfa(a, 0x40000000, 1); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_bad))
check('result_of broken (access)', reasons(facts), {'W': ['origin-violates']})


def res_share(a):
    a.func('f1'); cfa(a, 0x80000000, 3); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_share))
check('result_of broken (share)', reasons(facts), {'W': ['origin-violates']})


def res_other(a):
    a.func('f1'); a.push_s(S_K32); a.call(LL); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_other))
check('result_of wrong callee', reasons(facts), {'W': ['origin-not-in-set']})


def res_imm(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_imm))
check('result_of immediate handle', reasons(facts), {'W': ['handle-open']})


def res_mem_strict(a):
    a.func('f1'); cfa(a, 0x80000000, 1); a.raw(0x89, 0x06)                      # mov [esi],eax
    cfm(a, lambda a: (a.raw(0x8b, 0x06), a.raw(0x50)), 2); a.ret(); a.end('f1')   # mov eax,[esi]; push eax


facts, certs, g = go(reqs(R_W), dict(a=res_mem_strict))
check('result_of through memory, no call between', (reasons(facts), len(certs)), ({'W': []}, 1))


def res_mem_open(a):
    a.func('f1'); cfa(a, 0x80000000, 1); a.raw(0x89, 0x06)
    a.push_s(S_K32); a.call(LL)                                                 # a call between store and load
    cfm(a, lambda a: (a.raw(0x8b, 0x06), a.raw(0x50)), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_mem_open))
check('result_of needs A7', (reasons(facts), len(certs)), ({'W': ['assumption:A7']}, 0))

# ---- determinism and hashes
rs1, rs2 = reqs(R_H, R_P, R_W, R_F), reqs(R_F, R_W, R_P, R_H)
check('ids independent of row order', [(r['id'], r['request']) for r in rs1], [(r['id'], r['request']) for r in rs2])
check('ids start at 1 in canonical order', [(r['id'], r['request'], r['k']) for r in rs1], [(1, 'F', 1), (2, 'H', 0), (3, 'W', 0), (4, 'P', 2)])
check('table id', gate_reach.table_id(rs1), gate_reach.table_id(rs2))
f1, c1, _ = go(reqs(R_H, R_P, R_F), dict(a=ok_img))
f2, c2, _ = go(reqs(R_F, R_P, R_H), dict(a=ok_img))
check('certificate hashes repeat', c1, c2)
f3, c3, _ = go(reqs(R_H, R_P, R_F), dict(a=ok_img, b=res_imm))
check('image set changes the hash', (set(c1) == set(c3), any(c1[i] == c3[i] for i in c1)), (True, False))
hdr = gate_reach.header(rs1)
for want in ('#define FNVNX_REACH_KERNEL32_CreateFileMappingA_1 2', '#define FNVNX_REACH_KERNEL32_CreateFileMappingA_3 4',
             '#define FNVNX_REACH_KERNEL32_CreateFileA_1 1', 'FNVNX_REACH_TABLE_ID "' + gate_reach.table_id(rs1) + '"'):
    check('header ' + want[:50], want in hdr, True)
check('header deterministic', hdr, gate_reach.header(rs2))
for bad in ('H\tkernel32\tX\t0\tnope\t1\tn\n', 'H\tkernel32\tX\t0\timm_set\tzz\tn\n'):
    try:
        gate_reach.parse_requests('\t'.join(gate_reach.COLUMNS) + '\n' + bad)
        check('bad row rejected', 'accepted', 'error')
    except gate_reach.FormatError:
        pass

# ---- gate files
out = tempfile.mkdtemp()
os.environ['GATE_OUT'] = out
facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=bad_img))
gate_reach.gate(facts, certs, g, 1)
with open(os.path.join(out, 'reach.json')) as fh:
    gj = fh.read()
check('gate json', ('"status":"red"' in gj, '"universe":3' in gj, '"certified":1' in gj, '"name":"reach"' in gj), (True,) * 4)
with open(os.path.join(out, 'reach.facts.tsv')) as fh:
    ft = fh.read().split('\n')
check('facts tsv only certified', [l.split('\t')[0] for l in ft if l], ['1'])
shutil.rmtree(out)
del os.environ['GATE_OUT']

# ---- link side
CC = shutil.which('aarch64-linux-gnu-gcc')
AR = shutil.which('aarch64-linux-gnu-ar')
if not CC:
    print('skipped link-side tests: no aarch64-linux-gnu-gcc')
else:
    td = tempfile.mkdtemp()

    def obj(name, src, asm=False):
        p = os.path.join(td, name + ('.s' if asm else '.c'))
        with open(p, 'w') as fh:
            fh.write(src)
        o = os.path.join(td, name + '.o')
        subprocess.run([CC, '-mabi=ilp32', '-O2', '-c', p, '-o', o], check=True)
        return o

    DECL = 'void fnvnx_reach_violation(unsigned) __attribute__((noreturn));\n'
    good = obj('good', DECL + 'int f(int x){ if(x) fnvnx_reach_violation(7); return 1; }\n'
                        'int g(int x){ if(x) fnvnx_reach_violation(0x123456); return 2; }\n')
    ids = lambda paths, n, cert: gate_reach.link_check(paths, n, cert)
    uni, red = ids([good], 0x200000, {7, 0x123456})
    check('link good', (uni, red), (2, []))
    uni, red = ids([good], 0x200000, {7})
    check('link uncertified', [r.split(':')[-1] for r in red], ['fact-not-certified'])
    check('link uncertified names the site', red[0].startswith('good.o:.text+'), True)
    uni, red = ids([good], 100, {7, 0x123456})
    check('link not in table', [r.split(':')[-1] for r in red], ['fact-not-in-table'])
    reg = obj('reg', DECL + 'void h(unsigned a){ if(a>3) fnvnx_reach_violation(a+1); }\n')
    check('link register argument', [r.split(':')[-1] for r in ids([reg], 100, {7})[1]], ['no-immediate'])
    ptr = obj('ptr', DECL + 'void (*fp)(unsigned) = fnvnx_reach_violation;\n')
    check('link pointer reference', [r.split(':')[-1] for r in ids([ptr], 100, {7})[1]], ['reference-not-call'])
    sw = obj('sw', DECL + 'int s(int x){ static void *t[]={&&a,&&b}; goto *t[x&1]; a: return 1; b: fnvnx_reach_violation(7); }\n')
    check('link switch table', [r.split(':')[-1] for r in ids([sw], 100, {7})[1]], ['indirect-jump'])
    A = '.text\n.global a1\n.type a1,%function\n'
    lab = obj('lab', A + 'a1: mov w0,#7\n.Lx: bl fnvnx_reach_violation\n b .Lx\n', True)
    check('link label before call', [r.split(':')[-1] for r in ids([lab], 100, {7})[1]], ['label'])
    lab2 = obj('lab2', A + 'a1: cbz w1,.Lx\n mov w0,#7\n mov w2,#1\n.Lx: bl fnvnx_reach_violation\n', True)
    check('link label at the call', [r.split(':')[-1] for r in ids([lab2], 100, {7})[1]], ['label'])
    rew = obj('rew', A + 'a1: mov w0,#7\n mov w0,w1\n bl fnvnx_reach_violation\n', True)
    check('link w0 rewritten', [r.split(':')[-1] for r in ids([rew], 100, {7})[1]], ['no-immediate'])
    cl = obj('cl', A + 'a1: mov w0,#7\n bl other\n bl fnvnx_reach_violation\n', True)
    check('link call between', [r.split(':')[-1] for r in ids([cl], 100, {7})[1]], ['block-boundary'])
    br = obj('br', A + 'a1: mov w0,#7\n cbz w1,.Lz\n bl fnvnx_reach_violation\n.Lz: ret\n', True)
    check('link branch between', sorted(r.split(':')[-1] for r in ids([br], 100, {7})[1]), ['block-boundary'])
    jm = obj('jm', A + 'a1: mov w0,#7\n b fnvnx_reach_violation\n', True)
    ij = obj('ij', A + 'a1: mov w0,#7\n bl fnvnx_reach_violation\n br x1\n', True)
    check('link indirect jump, no function size', [r.split(':')[-1] for r in ids([ij], 100, {7})[1]], ['indirect-jump'])
    ij2 = obj('ij2', A + 'a1: mov w0,#7\n bl fnvnx_reach_violation\n.size a1,.-a1\n.global a2\n.type a2,%function\na2: br x1\n.size a2,.-a2\n', True)
    check('link indirect jump in another function', ids([ij2], 100, {7}), (1, []))
    check('link tail jump', ids([jm], 100, {7}), (1, []))
    mk = obj('mk', A + 'a1: movz w0,#3\n movk w0,#1,lsl #16\n nop\n bl fnvnx_reach_violation\n', True)
    check('link movz/movk', ids([mk], 0x20000, {0x10003}), (1, []))
    check('link movz/movk value', [r.split(':')[-1] for r in ids([mk], 0x20000, {3})[1]], ['fact-not-certified'])
    if AR:
        arc = os.path.join(td, 'lib.a')
        subprocess.run([AR, 'rcs', arc, good, reg], check=True)
        uni, red = ids([arc], 100, {7})
        check('link archive members', (uni, [r.split(':')[0] + ':' + r.split(':')[-1] for r in red]),
              (3, ['lib.a(good.o):fact-not-in-table', 'lib.a(reg.o):no-immediate']))
        rsp = os.path.join(td, 'inputs.rsp')
        with open(rsp, 'w') as fh:
            fh.write('"%s"\n' % good)
        rq, ct = os.path.join(td, 'q.tsv'), os.path.join(td, 'c.tsv')
        with open(rq, 'w') as fh:
            fh.write('\t'.join(gate_reach.COLUMNS) + '\n' + '\t'.join(R_H) + '\n')
        with open(ct, 'w') as fh:
            fh.write('1\tabc\n')
        # id 1 is certified but the objects use 7 and 0x123456: both outside the one-row table
        check('main --objects', gate_reach.main(['--objects', rsp, '--requests', rq, '--certified', ct]), 1)
    shutil.rmtree(td)

# ---- closure_key: every value reaching a dispatcher is a key of its table
SRC_OK = ''.join('%s\tclosed\n' % x for x in gate_reach.CLOSURE_SOURCES)
CK = ('REACH_CLOSURE_KEY_LIFTED', '-', 'fnvl_dispatch', '0', 'closure_key', 'dispatcher=lifted', 'n')
CK2 = ('REACH_CLOSURE_KEY_NATIVE', '-', 'fnvnx_dispatch', '0', 'closure_key', 'dispatcher=native', 'n')
LL_OK = 'module\tsite\tcalls\tapi\tstatus\tnames\tbreaks\nexe\t401000\t1\tLoadLibraryA\tclosed\tx.dll\t-\n'
LL_OPEN = LL_OK + 'exe\t402000\t1\tLoadLibraryA\topen\t-\tx\n'


def ck(entries, closure, sources=SRC_OK, loadlib=LL_OK, rows=(CK,)):
    ctx = {'dispatchers': {'lifted': (entries, closure, sources), 'native': (entries, closure, sources)}, 'loadlib': loadlib}
    rs = reqs(*rows)
    facts, certs, g = gate_reach.run(rs, {}, [], [], ctx)
    return rs, facts, certs


ENT = '00401000\tf1\n00401010\tf2\n'
rs, facts, certs = ck(ENT, '00401000\n00401010\n')
check('closure_key: everything closed is certified', (len(certs), reasons(facts)), (1, {'REACH_CLOSURE_KEY_LIFTED': []}))
CKF = ('REACH_CLOSURE_KEY_NATIVE', '-', 'fnvnx_dispatch', '0', 'closure_key', 'dispatcher=native;miss=fallthrough', 'n')
rs, facts, certs = ck(ENT, '00401000\n00401010\n00401020\n', rows=(CKF,))
check('closure_key fallthrough: keys inside the closure are certified, closure addresses without a key are fine', (len(certs), reasons(facts)), (1, {'REACH_CLOSURE_KEY_NATIVE': []}))
rs, facts, certs = ck(ENT, '00401000\n', rows=(CKF,))
check('closure_key fallthrough: a key outside the closure is red', (certs, sorted(facts[0].reasons)), ({}, [('key-outside-closure', 'native', '0x401010')]))
rs, facts, certs = ck(ENT, '00401000\n00401020\n')
check('closure_key: a closure address without a key is red', (certs, sorted(facts[0].reasons)), ({}, [('closure-missing', 'lifted', '0x401020')]))
rs, facts, certs = ck(ENT, '00401000\n', sources=SRC_OK.replace('eh\tclosed', 'eh\topen'))
check('closure_key: an open source of the closure is red', (certs, sorted(facts[0].reasons)), ({}, [('source-open', 'lifted', 'eh')]))
rs, facts, certs = ck(ENT, '00401000\n', sources=SRC_OK.replace('tls\tclosed\n', ''))
check('closure_key: an absent source is red', sorted(facts[0].reasons), [('source-open', 'lifted', 'tls')])
rs, facts, certs = ck(ENT, '00401000\n', loadlib=LL_OPEN)
check('closure_key: an open LoadLibrary site is measurement, never valid', (certs, sorted({r[0] for r in facts[0].reasons}),
      gate_reach.report_tsv(facts, certs).split('\n')[1].split('\t')[5]), ({}, ['loadlib-open'], 'measurement'))
rs, facts, certs = ck(ENT, '00401020\n', loadlib=LL_OPEN)
check('closure_key: a missing key with open loads stays red (uncertified), not measurement', gate_reach.report_tsv(facts, certs).split('\n')[1].split('\t')[5], 'uncertified')
rs, facts, certs = ck(ENT, '00401000\n', loadlib=None)
check('closure_key: no loadlib.tsv is open', sorted({r[0] for r in facts[0].reasons}), ['loadlib-open'])
rs, facts, certs = ck(ENT, '00401000\n', rows=(CK2,))
check('closure_key: one id per dispatcher, names bound by --dispatcher', (len(certs), rs[0]['spec']), (1, {'dispatcher': 'native', 'fallthrough': False}))
rs = reqs(CK, CK2)
facts, certs, g = gate_reach.run(rs, {}, [], [], {'dispatchers': {'native': (ENT, '00401000\n', SRC_OK)}, 'loadlib': LL_OK})
check('closure_key: a dispatcher without inputs is red', (sorted(certs), [sorted(f.reasons)[0][0] for f in facts if f.reasons]), ([2], ['dispatcher-missing']))
h = gate_reach.header(rs)
check('closure_key: FNVNX_REACH_* macros, one id per dispatcher, each defined once', (h.count('#define FNVNX_REACH_CLOSURE_KEY_LIFTED 1 '), h.count('#define FNVNX_REACH_CLOSURE_KEY_NATIVE 2 '),
      h.count('#define FNVNX_REACH_CLOSURE_KEY_LIFTED'), h.count('#define FNVNX_REACH_CLOSURE_KEY_NATIVE')), (1, 1, 1, 1))
for bad in (('X', 'k.dll', 'f', '0', 'closure_key', 'dispatcher=a', ''), ('X', '-', 'f', '0', 'closure_key', 'a', ''), ('X', '-', 'f', '0', 'closure_key', 'dispatcher=1 x', '')):
    try:
        reqs(bad)
        check('closure_key: malformed row is an error %r' % (bad[1:],), 'parsed', 'error')
    except gate_reach.FormatError:
        pass

# reachreq.py (the request table and the one header emitter): its CLI writes the header that gate_reach.header builds from the same table,
# through the function the full mode uses (write_facts_h); gate_reach re-exports the emitter, so there is one
tdir = tempfile.mkdtemp(prefix='reach-h-')
RR = os.path.join(HERE, '..', 'reachreq.py')
import reachreq
check('gate_reach uses the one emitter and parser of reachreq', (gate_reach.header is reachreq.header, gate_reach.parse_requests is reachreq.parse_requests,
      gate_reach.write_facts_h is reachreq.write_facts_h), (True, True, True))
for nm, rows_ in (('one', (CK,)), ('two', (CK, CK2)), ('cfm', (R_H, R_P, R_W))):
    tsv = os.path.join(tdir, nm + '.tsv')
    open(tsv, 'w').write('\t'.join(gate_reach.COLUMNS) + '\n' + ''.join('\t'.join(r) + '\n' for r in rows_))
    outh = os.path.join(tdir, 'sub', nm + '.h')
    rc_ = subprocess.run([sys.executable, '-s', '-B', '-I', RR, '--requests', tsv, '--facts-h', outh], capture_output=True).returncode
    check('reachreq CLI %s: exit 0 and the header equals gate_reach.header(parse_requests(table)) (output dir created)' % nm,
          (rc_, open(outh).read()), (0, gate_reach.header(gate_reach.parse_requests(open(tsv).read()))))
rc_ = subprocess.run([sys.executable, '-s', '-B', '-I', RR, '--requests', os.path.join(tdir, 'nope.tsv'), '--facts-h', outh], capture_output=True).returncode
check('reachreq CLI on a missing table is an error', rc_ != 0, True)
open(os.path.join(tdir, 'bad.tsv'), 'w').write('request\tx\n')
rc_ = subprocess.run([sys.executable, '-s', '-B', '-I', RR, '--requests', os.path.join(tdir, 'bad.tsv'), '--facts-h', outh], capture_output=True).returncode
check('reachreq CLI on a malformed table exits 2', rc_, 2)
shutil.rmtree(tdir, ignore_errors=True)

if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok test_gate_reach')
