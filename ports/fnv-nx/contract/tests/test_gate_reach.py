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
import gate_reach
import gpa_names
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

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
    struct.pack_into('<II', hdr, o + 96 + 40, 0x4000, len(reloc))
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
    img = argflow.Image(data, name, ranges)
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

fails = testcase.FAILED
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
case('certified imm', got=(reasons(facts), len(certs), g), want=({'H': [], 'P': [], 'F': []}, 3, []))


def bad_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0xff), 4); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_H, R_P), dict(a=bad_img))
case('forbidden', got=(reasons(facts), len(certs)), want=({'H': ['forbidden-immediate'], 'P': ['forbidden-bits']}, 0))
call_va = [c for c in argflow.imports_called(image(bad_img, 'a')[0], 'CreateFileMappingA')[0]][0]
case('site named', got=sites(facts, 'H'), want=['0x%x' % call_va])


def nonconst_img(a):
    a.func('f1'); cfm(a, lambda a: a.imm([0xff, 0x35], G_VAR), 2); a.ret(); a.end('f1')   # push [global]


facts, certs, g = go(reqs(R_H), dict(a=nonconst_img))
case('non-constant', got=reasons(facts), want={'H': ['arg-open']})


def ret_img(a):
    a.func('f1'); cfa(a, 0x80000000, 1); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_H), dict(a=ret_img))
case('call result is not an immediate', got=reasons(facts), want={'H': ['not-immediate']})

# ---- GetProcAddress
def gpa_hit_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')
    a.func('g1'); gpa_const(a, S_CFM); a.ret(); a.end('g1')


facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=gpa_hit_img))
case('gpa pair', got=(reasons(facts), len(certs)), want=({'H': ['gpa-pair'], 'P': ['gpa-pair'], 'F': []}, 1))


def gpa_open_img(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')
    a.func('g1'); a.push_s(S_K32); a.call(LL); a.push_s(S_WR); a.raw(0x50); a.call(GPA); a.ret(); a.end('g1')   # name in a writable section


facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=gpa_open_img))
case('gpa open', got=(reasons(facts), len(certs), len(g)), want=({'H': ['gpa-open'], 'P': ['gpa-open'], 'F': ['gpa-open']}, 0, 1))
res_ids = gate_reach.uncertified_ids(facts)
case('ids format', got=res_ids[0].split(':')[:2], want=['1', 'gpa-open'])
case('ids tail', got=[x.split(':')[-2] for x in res_ids], want=['a'] * 3)
facts, certs, g = go(reqs(R_H), dict(a=ok_img), skip={('a', r) for r in image(ok_img, 'a')[0].relocs if image(ok_img, 'a')[0].word(r) == GPA})
case('gpa uncovered', got=reasons(facts), want={'H': ['gpa-uncovered']})

# ---- result_of
def res_ok(a):
    a.func('f1'); cfa(a, 0x80000000, 1); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_ok))
case('result_of proven', got=(reasons(facts), len(certs)), want=({'W': []}, 1))
facts, certs, g = go(reqs(R_W2), dict(a=res_ok))
case('handle_origin spelling', got=(reasons(facts), len(certs)), want=({'W': []}, 1))


def res_bad(a):
    a.func('f1'); cfa(a, 0x40000000, 1); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_bad))
case('result_of broken (access)', got=reasons(facts), want={'W': ['origin-violates']})


def res_share(a):
    a.func('f1'); cfa(a, 0x80000000, 3); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_share))
case('result_of broken (share)', got=reasons(facts), want={'W': ['origin-violates']})


def res_other(a):
    a.func('f1'); a.push_s(S_K32); a.call(LL); cfm(a, lambda a: a.raw(0x50), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_other))
case('result_of wrong callee', got=reasons(facts), want={'W': ['origin-not-in-set']})


def res_imm(a):
    a.func('f1'); cfm(a, lambda a: a.push_b(0x10), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_imm))
case('result_of immediate handle', got=reasons(facts), want={'W': ['handle-open']})


def res_mem_strict(a):
    a.func('f1'); cfa(a, 0x80000000, 1); a.raw(0x89, 0x06)                      # mov [esi],eax
    cfm(a, lambda a: (a.raw(0x8b, 0x06), a.raw(0x50)), 2); a.ret(); a.end('f1')   # mov eax,[esi]; push eax


facts, certs, g = go(reqs(R_W), dict(a=res_mem_strict))
case('result_of through memory, no call between', got=(reasons(facts), len(certs)), want=({'W': []}, 1))


def res_mem_open(a):
    a.func('f1'); cfa(a, 0x80000000, 1); a.raw(0x89, 0x06)
    a.push_s(S_K32); a.call(LL)                                                 # a call between store and load
    cfm(a, lambda a: (a.raw(0x8b, 0x06), a.raw(0x50)), 2); a.ret(); a.end('f1')


facts, certs, g = go(reqs(R_W), dict(a=res_mem_open))
case('result_of needs A7', got=(reasons(facts), len(certs)), want=({'W': ['assumption:A7']}, 0))

# ---- determinism and hashes
rs1, rs2 = reqs(R_H, R_P, R_W, R_F), reqs(R_F, R_W, R_P, R_H)
case('ids independent of row order', got=[(r['id'], r['request']) for r in rs1], want=[(r['id'], r['request']) for r in rs2])
case('ids start at 1 in canonical order', got=[(r['id'], r['request'], r['k']) for r in rs1], want=[(1, 'F', 1), (2, 'H', 0), (3, 'W', 0), (4, 'P', 2)])
case('table id', got=gate_reach.table_id(rs1), want=gate_reach.table_id(rs2))
f1, c1, _ = go(reqs(R_H, R_P, R_F), dict(a=ok_img))
f2, c2, _ = go(reqs(R_F, R_P, R_H), dict(a=ok_img))
case('certificate hashes repeat', got=c1, want=c2)
f3, c3, _ = go(reqs(R_H, R_P, R_F), dict(a=ok_img, b=res_imm))
case('image set changes the hash', got=(set(c1) == set(c3), any(c1[i] == c3[i] for i in c1)), want=(True, False))
hdr = gate_reach.header(rs1)
for want in ('#define FNVNX_REACH_KERNEL32_CreateFileMappingA_1 2', '#define FNVNX_REACH_KERNEL32_CreateFileMappingA_3 4',
             '#define FNVNX_REACH_KERNEL32_CreateFileA_1 1', 'FNVNX_REACH_TABLE_ID "' + gate_reach.table_id(rs1) + '"'):
    case('header ', got=want in hdr, want=True, key=want[:50])
case('header deterministic', got=hdr, want=gate_reach.header(rs2))
for bad in ('H\tkernel32\tX\t0\tnope\t1\tn\n', 'H\tkernel32\tX\t0\timm_set\tzz\tn\n'):
    try:
        gate_reach.parse_requests('\t'.join(gate_reach.COLUMNS) + '\n' + bad)
        case('bad row rejected', got='accepted', want='error')
    except gate_reach.FormatError:
        pass

# ---- gate files
out = tempfile.mkdtemp()
os.environ['GATE_OUT'] = out
facts, certs, g = go(reqs(R_H, R_P, R_F), dict(a=bad_img))
gate_reach.gate(facts, certs, g, 1)
with open(os.path.join(out, 'reach.json')) as fh:
    gj = fh.read()
case('gate json', got=('"status":"red"' in gj, '"universe":3' in gj, '"certified":1' in gj, '"name":"reach"' in gj), want=(True,) * 4)
with open(os.path.join(out, 'reach.facts.tsv')) as fh:
    ft = fh.read().split('\n')
case('facts tsv only certified', got=[l.split('\t')[0] for l in ft if l], want=['1'])
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
    case('link good', got=(uni, red), want=(2, []))
    uni, red = ids([good], 0x200000, {7})
    case('link uncertified', got=[r.split(':')[-1] for r in red], want=['fact-not-certified'])
    case('link uncertified names the site', got=red[0].startswith('good.o:.text+'), want=True)
    uni, red = ids([good], 100, {7, 0x123456})
    case('link not in table', got=[r.split(':')[-1] for r in red], want=['fact-not-in-table'])
    reg = obj('reg', DECL + 'void h(unsigned a){ if(a>3) fnvnx_reach_violation(a+1); }\n')
    case('link register argument', got=[r.split(':')[-1] for r in ids([reg], 100, {7})[1]], want=['no-immediate'])
    ptr = obj('ptr', DECL + 'void (*fp)(unsigned) = fnvnx_reach_violation;\n')
    case('link pointer reference', got=[r.split(':')[-1] for r in ids([ptr], 100, {7})[1]], want=['reference-not-call'])
    sw = obj('sw', DECL + 'int s(int x){ static void *t[]={&&a,&&b}; goto *t[x&1]; a: return 1; b: fnvnx_reach_violation(7); }\n')
    case('link switch table', got=[r.split(':')[-1] for r in ids([sw], 100, {7})[1]], want=['indirect-jump'])
    A = '.text\n.global a1\n.type a1,%function\n'
    lab = obj('lab', A + 'a1: mov w0,#7\n.Lx: bl fnvnx_reach_violation\n b .Lx\n', True)
    case('link label before call', got=[r.split(':')[-1] for r in ids([lab], 100, {7})[1]], want=['label'])
    lab2 = obj('lab2', A + 'a1: cbz w1,.Lx\n mov w0,#7\n mov w2,#1\n.Lx: bl fnvnx_reach_violation\n', True)
    case('link label at the call', got=[r.split(':')[-1] for r in ids([lab2], 100, {7})[1]], want=['label'])
    rew = obj('rew', A + 'a1: mov w0,#7\n mov w0,w1\n bl fnvnx_reach_violation\n', True)
    case('link w0 rewritten', got=[r.split(':')[-1] for r in ids([rew], 100, {7})[1]], want=['no-immediate'])
    cl = obj('cl', A + 'a1: mov w0,#7\n bl other\n bl fnvnx_reach_violation\n', True)
    case('link call between', got=[r.split(':')[-1] for r in ids([cl], 100, {7})[1]], want=['block-boundary'])
    br = obj('br', A + 'a1: mov w0,#7\n cbz w1,.Lz\n bl fnvnx_reach_violation\n.Lz: ret\n', True)
    case('link branch between', got=sorted(r.split(':')[-1] for r in ids([br], 100, {7})[1]), want=['block-boundary'])
    jm = obj('jm', A + 'a1: mov w0,#7\n b fnvnx_reach_violation\n', True)
    ij = obj('ij', A + 'a1: mov w0,#7\n bl fnvnx_reach_violation\n br x1\n', True)
    case('link indirect jump, no function size', got=[r.split(':')[-1] for r in ids([ij], 100, {7})[1]], want=['indirect-jump'])
    ij2 = obj('ij2', A + 'a1: mov w0,#7\n bl fnvnx_reach_violation\n.size a1,.-a1\n.global a2\n.type a2,%function\na2: br x1\n.size a2,.-a2\n', True)
    case('link indirect jump in another function', got=ids([ij2], 100, {7}), want=(1, []))
    case('link tail jump', got=ids([jm], 100, {7}), want=(1, []))
    mk = obj('mk', A + 'a1: movz w0,#3\n movk w0,#1,lsl #16\n nop\n bl fnvnx_reach_violation\n', True)
    case('link movz/movk', got=ids([mk], 0x20000, {0x10003}), want=(1, []))
    case('link movz/movk value', got=[r.split(':')[-1] for r in ids([mk], 0x20000, {3})[1]], want=['fact-not-certified'])
    if AR:
        arc = os.path.join(td, 'lib.a')
        subprocess.run([AR, 'rcs', arc, good, reg], check=True)
        uni, red = ids([arc], 100, {7})
        case('link archive members', got=(uni, [r.split(':')[0] + ':' + r.split(':')[-1] for r in red]),
              want=(3, ['lib.a(good.o):fact-not-in-table', 'lib.a(reg.o):no-immediate']))
        rsp = os.path.join(td, 'inputs.rsp')
        with open(rsp, 'w') as fh:
            fh.write('"%s"\n' % good)
        rq, ct = os.path.join(td, 'q.tsv'), os.path.join(td, 'c.tsv')
        with open(rq, 'w') as fh:
            fh.write('\t'.join(gate_reach.COLUMNS) + '\n' + '\t'.join(R_H) + '\n')
        with open(ct, 'w') as fh:
            fh.write('1\tabc\n')
        # id 1 is certified but the objects use 7 and 0x123456: both outside the one-row table
        case('main --objects', got=gate_reach.main(['--objects', rsp, '--requests', rq, '--certified', ct]), want=1)
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
case('closure_key: everything closed is certified', got=(len(certs), reasons(facts)), want=(1, {'REACH_CLOSURE_KEY_LIFTED': []}))
CKF = ('REACH_CLOSURE_KEY_NATIVE', '-', 'fnvnx_dispatch', '0', 'closure_key', 'dispatcher=native;miss=fallthrough', 'n')
rs, facts, certs = ck(ENT, '00401000\n00401010\n00401020\n', rows=(CKF,))
case('closure_key fallthrough: keys inside the closure are certified, closure addresses without a key are fine', got=(len(certs), reasons(facts)), want=(1, {'REACH_CLOSURE_KEY_NATIVE': []}))
rs, facts, certs = ck(ENT, '00401000\n', rows=(CKF,))
case('closure_key fallthrough: a key outside the closure is red', got=(certs, sorted(facts[0].reasons)), want=({}, [('key-outside-closure', 'native', '0x401010')]))
rs, facts, certs = ck(ENT, '00401000\n00401020\n')
case('closure_key: a closure address without a key is red', got=(certs, sorted(facts[0].reasons)), want=({}, [('closure-missing', 'lifted', '0x401020')]))
rs, facts, certs = ck(ENT, '00401000\n', sources=SRC_OK.replace('eh\tclosed', 'eh\topen'))
case('closure_key: an open source of the closure is red', got=(certs, sorted(facts[0].reasons)), want=({}, [('source-open', 'lifted', 'eh')]))
rs, facts, certs = ck(ENT, '00401000\n', sources=SRC_OK.replace('tls\tclosed\n', ''))
case('closure_key: an absent source is red', got=sorted(facts[0].reasons), want=[('source-open', 'lifted', 'tls')])
rs, facts, certs = ck(ENT, '00401000\n', loadlib=LL_OPEN)
case('closure_key: an open LoadLibrary site is measurement, never valid', got=(certs, sorted({r[0] for r in facts[0].reasons}),
      gate_reach.report_tsv(facts, certs).split('\n')[1].split('\t')[5]), want=({}, ['loadlib-open'], 'measurement'))
rs, facts, certs = ck(ENT, '00401020\n', loadlib=LL_OPEN)
case('closure_key: a missing key with open loads stays red (uncertified), not measurement', got=gate_reach.report_tsv(facts, certs).split('\n')[1].split('\t')[5], want='uncertified')
rs, facts, certs = ck(ENT, '00401000\n', loadlib=None)
case('closure_key: no loadlib.tsv is open', got=sorted({r[0] for r in facts[0].reasons}), want=['loadlib-open'])
rs, facts, certs = ck(ENT, '00401000\n', rows=(CK2,))
case('closure_key: one id per dispatcher, names bound by --dispatcher', got=(len(certs), rs[0]['spec']), want=(1, {'dispatcher': 'native', 'fallthrough': False}))
rs = reqs(CK, CK2)
facts, certs, g = gate_reach.run(rs, {}, [], [], {'dispatchers': {'native': (ENT, '00401000\n', SRC_OK)}, 'loadlib': LL_OK})
case('closure_key: a dispatcher without inputs is red', got=(sorted(certs), [sorted(f.reasons)[0][0] for f in facts if f.reasons]), want=([2], ['dispatcher-missing']))
h = gate_reach.header(rs)
case('closure_key: FNVNX_REACH_* macros, one id per dispatcher, each defined once', got=(h.count('#define FNVNX_REACH_CLOSURE_KEY_LIFTED 1 '), h.count('#define FNVNX_REACH_CLOSURE_KEY_NATIVE 2 '),
      h.count('#define FNVNX_REACH_CLOSURE_KEY_LIFTED'), h.count('#define FNVNX_REACH_CLOSURE_KEY_NATIVE')), want=(1, 1, 1, 1))
for bad in (('X', 'k.dll', 'f', '0', 'closure_key', 'dispatcher=a', ''), ('X', '-', 'f', '0', 'closure_key', 'a', ''), ('X', '-', 'f', '0', 'closure_key', 'dispatcher=1 x', '')):
    try:
        reqs(bad)
        case('closure_key: malformed row is an error %r', got='parsed', want='error', key=(bad[1:],))
    except gate_reach.FormatError:
        pass

if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok test_gate_reach')
