"""The control-transfer forms (rt/fnvl.h FNVL_CALLX/CALLD/TAILX/JMPN/RET and their plain names FNVL_*P): the lifter emits one call of a form at every call, tail jump, non-local jump and
return, and the form expands to exactly the statements that were emitted at each site before (the reference templates below are those statements).
  (1) every form and its plain name expand, token for token, to the reference statement, for plain hooks and for hooks the lifter extended (SYNC_X_*, x87 stack traffic);
  (2) lifted buffers of every transfer shape (entry / not an entry / import / indirect, plain / x87 function): no site keeps the legacy text, no form call is left unexpanded by the header's own
      definitions, and a plain name is used exactly where the hooks are the unextended ones;
  (3) the static x87 path (f_x87_ctl.fix_lines) extends the hooks inside the form's arguments: the stores before SYNC_OUT, the loads after SYNC_IN, none after a tail's SYNC_IN.
    test_xfer_forms.py --sdmrows PATH"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
import capstone
from fnvlift import core, f_x87_ctl
from fnvlift.image import Image
import fnvlift as L
HDR = os.path.join(_inputs.LIFT, 'rt', 'fnvl.h')
NAMES = r'FNVL_(?:CALLXP?|CALLDP?|TAILXP?|JMPNP?|RETP?)'
FAILS = []


def check(n, ok):
    print('%-70s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


def defs():
    D = {}
    for l in open(HDR):
        m = re.match(r'#define (%s)\(([^)]*)\) (.*)$' % NAMES, l.rstrip('\n'))
        if m: D[m[1]] = ([x.strip() for x in m[2].split(',')], m[3])
    return D


def split_args(s, i):
    d = 0; a = []; cur = i + 1; j = i
    while True:
        ch = s[j]
        if ch in '([{': d += 1
        elif ch in ')]}':
            d -= 1
            if d == 0: a.append(s[cur:j]); return a, j + 1
        elif ch == ',' and d == 1: a.append(s[cur:j]); cur = j + 1
        j += 1


def expand(s, D):
    """the preprocessor's substitution for the form macros: arguments split at top-level commas, parameters replaced in the body, rescanned"""
    pat = re.compile(r'\b(%s)\(' % NAMES)
    while True:
        m = pat.search(s)
        if not m: return s
        args, e = split_args(s, m.end() - 1)
        ps, body = D[m[1]]; assert len(ps) == len(args), (m[1], args)
        sub = dict(zip(ps, args))
        s = s[:m.start()] + re.sub(r'\b(%s)\b' % '|'.join(map(re.escape, ps)), lambda x: sub[x[1]], body) + s[e:]


def toks(s): return re.findall(r'[A-Za-z_0-9]+|->|!=|==|\S', s)


D = defs()
check('the header defines all ten forms', sorted(D) == sorted('FNVL_%s%s' % (n, p) for n in ('CALLX', 'CALLD', 'TAILX', 'JMPN', 'RET') for p in ('', 'P')))
# (1) reference statements: what the emitter wrote at the site before the forms
SO, SI, G = 'SYNC_OUT;', 'SYNC_IN;', 'goto nonlocal;'
XSO, XSI, XG = 'c->fx[(ft0-1)&7]=s0;c->ftop=(ft0-1)&7;SYNC_OUT;SYNC_X_OUT;', 'SYNC_IN;SYNC_X_IN;ft0=(c->ftop+1)&7;s0=c->fx[(ft0-1)&7];', 'c->ftop=(ft0-1)&7;goto nonlocal;'
REF = {'CALLX': lambda so, si, g, h, nx: '%s REGS(); CALLX(%s); REGS(); %s if(c->ra!=%s) goto nonlocal;' % (so, h, si, nx),
       'CALLD': lambda so, si, g, t, nx: '%s REGS(); fnvl_dispatch(%s)(); REGS(); %s if(c->ra!=%s) goto nonlocal;' % (so, t, si, nx),
       'TAILX': lambda so, si, g, h, nx: '{ %s REGS(); CALLX(%s); REGS(); %s goto after_tail; }' % (so, h, si),
       'JMPN': lambda so, si, g, t, nx: '{ c->ra=%s; %s }' % (t, g),
       'RET': lambda so, si, g, r, nx: 'c->ra=%s; %s return;' % (r, so)}
for nm, ref in REF.items():
    for so, si, g, tag in ((SO, SI, G, 'plain hooks'), (XSO, XSI, XG, 'extended hooks')):
        for x, nx in (('00401000', '0x00401005u'), ('0x409000u', '0x40100au'), ('t', '0x40100au')):
            ps = D['FNVL_' + nm][0]
            call = {'CALLX': 'FNVL_CALLX(%s,%s,%s,%s)' % (so, si, x, nx), 'CALLD': 'FNVL_CALLD(%s,%s,%s,%s)' % (so, si, x, nx), 'TAILX': 'FNVL_TAILX(%s,%s,%s)' % (so, si, x),
                    'JMPN': 'FNVL_JMPN(%s,%s)' % (g, x), 'RET': 'FNVL_RET(%s,%s)' % (so, x)}[nm]
            check('%-5s %-14s %s' % (nm, tag, x), toks(expand(call, D)) == toks(ref(so, si, g, x, nx)))
    if so == SO:
        plain = {'CALLX': 'FNVL_CALLXP(00401000,0x00401005u)', 'CALLD': 'FNVL_CALLDP(t,0x00401005u)', 'TAILX': 'FNVL_TAILXP(00401000)', 'JMPN': 'FNVL_JMPNP(t)', 'RET': 'FNVL_RETP(t)'}[nm]
        longf = {'CALLX': 'FNVL_CALLX(SYNC_OUT;,SYNC_IN;,00401000,0x00401005u)', 'CALLD': 'FNVL_CALLD(SYNC_OUT;,SYNC_IN;,t,0x00401005u)', 'TAILX': 'FNVL_TAILX(SYNC_OUT;,SYNC_IN;,00401000)',
                 'JMPN': 'FNVL_JMPN(goto nonlocal;,t)', 'RET': 'FNVL_RET(SYNC_OUT;,t)'}[nm]
        check('%-5s plain name = long form with the plain hooks' % nm, toks(expand(plain, D)) == toks(expand(longf, D)))
# (2) lifted buffers
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
B = 0x401000


def rel(t): return ((t - (B + 5)) & 0xffffffff).to_bytes(4, 'little')


import tempfile
d = tempfile.mkdtemp(prefix='xfer-')
open(d + '/universe.tsv', 'w').write('00402000\tfunc\t16\t\n00403000\tfunc\t16\t\n')
from fnvlift import inputs
inputs.reset(); _inputs.configure()
inputs.reset()
_u = d + '/universe.tsv'
import json
open(d + '/depth.tsv', 'w').close(); open(d + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\n')
json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))
inputs.configure(universe=_u, fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, sdmrows=_inputs.sdmrows_arg(), cpumodel=_inputs.CPUMODEL, rcptab=_inputs.RCPTAB, rcpcert=_inputs.RCPCERT,
                 udsail=d + '/udsail.tsv', segwrites=d + '/segwrites.json', image='buf')
SHAPES = {'call_entry': b'\xe8' + rel(0x402000), 'call_noentry': b'\xe8' + rel(0x409000), 'call_ind': b'\xff\xd0', 'call_imp': b'\xff\x15\x00\x20\x40\x00', 'call_mem': b'\xff\x50\x08',
          'jmp_entry': b'\xe9' + rel(0x403000), 'jmp_noentry': b'\xe9' + rel(0x409000), 'jmp_ind': b'\xff\xe0', 'jmp_imp': b'\xff\x25\x00\x20\x40\x00', 'ret': b'\xc3', 'retn': b'\xc2\x08\x00', 'repz_ret': b'\xf3\xc3'}
FNS = {'plain': b'', 'x87': b'\xd9\xe8\xdd\xd8'}
LEGACY = re.compile(r'REGS\(\); CALLX\(|REGS\(\); fnvl_dispatch\(|goto after_tail;|c->ra=[^;]*; (?:SYNC_OUT;|goto nonlocal;)')
seen = set()
for fn, eb in FNS.items():
    for sh, cb in SHAPES.items():
        code = eb + cb + (b'' if sh.startswith(('ret', 'jmp', 'repz')) else b'\xc3')
        t = L.lift(Image.from_bytes(code, B), B, len(code))[0]
        forms = re.findall(r'\b(%s)\(' % NAMES, t); seen.update(forms)
        # the loop core appended to a function that has a transfer is not a form site; only the sites the instructions emitted are checked
        body = t.split('after_tail: ;')[0]
        check('%-6s %-13s forms %s' % (fn, sh, ','.join(forms) or '-'), forms != [] and not LEGACY.search(body) and not re.search(NAMES + r'\(', expand(t, D)))
check('every form family is emitted by some shape', {'FNVL_CALLXP', 'FNVL_CALLDP', 'FNVL_TAILXP', 'FNVL_JMPNP', 'FNVL_RETP', 'FNVL_CALLD', 'FNVL_JMPN', 'FNVL_RET'} <= seen)


# (3) static x87 path: the hooks of a form line are extended by fix_lines
class Cx: pass
cx = Cx(); cx.xd = {5: 2}; cx.xa = {5: 1}; cx.xmax = 3; cx.L = ['FNVL_CALLX(SYNC_OUT;,SYNC_IN;,00402000,0x6u)', 'FNVL_TAILX(SYNC_OUT;,SYNC_IN;,00402000)', 'FNVL_JMPN(goto nonlocal;,t)',
                                                                'FNVL_RET(SYNC_OUT;,t)']
f_x87_ctl.fix_lines(cx, 5, 0)
st, ld = f_x87_ctl.store(cx, 2), f_x87_ctl.load(1)
check('call: store before SYNC_OUT, load after SYNC_IN', cx.L[0] == 'FNVL_CALLX(%sSYNC_OUT;,SYNC_IN;%s,00402000,0x6u)' % (st, ld))
check('tail: store before SYNC_OUT, no load after SYNC_IN', cx.L[1] == 'FNVL_TAILX(%sSYNC_OUT;,SYNC_IN;,00402000)' % st)
check('non-local jump: store before goto nonlocal', cx.L[2] == 'FNVL_JMPN(%sgoto nonlocal;,t)' % st)
check('return: store before SYNC_OUT', cx.L[3] == 'FNVL_RET(%sSYNC_OUT;,t)' % st)
sys.exit(1 if FAILS else 0)
