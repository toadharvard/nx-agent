#!/usr/bin/env python3
"""ctrans_host: runs the compiled C of lift/rt on the host so that a ctrans term can be compared with it (a check of the transcription, it proves nothing).

The functions are compiled from the very text ctrans parsed (Source.funcs), by the pinned compiler (g++-13 13.3.0-6ubuntu2~24.04.1 of verify/env.json, `-x c`), with a generated
wrapper per function: `void w_NAME(const u64 *in, u64 *out)`; the argument words are the flattened parameters in order (a scalar is one word, a struct its members, a pointer to a
scalar one in/out cell, a pointer to u8 sixteen bytes in two words), the result words are the return value then the final contents of the cells.  The host is x86-64: the
default NaN of an invalid operation differs from Arm's, but every such result in the closure passes through fxn_slow (x87 rule), which does not read it; ctrans models the Arm one.
"""
import ctypes, os, re, subprocess, sys, tempfile
import z3
from pycparser import c_generator, c_ast
import ctrans

PINNED = '13.3.0-6ubuntu2~24.04.1'


def pinned_ok():
    try:
        v = subprocess.run(['dpkg-query', '-W', '-f=${Version}', 'g++-13'], capture_output=True, text=True).stdout.strip()
    except OSError:
        return False
    return v == PINNED and os.path.exists('/usr/bin/g++')


def flat(mc, t):
    if t[0] in 'if': return [t]
    if t[0] == 's': return [x for _, ft in mc.structs[t[1]] for x in flat(mc, ft)]
    raise ctrans.Unsupported('flat ' + str(t))


class Host:
    def __init__(self, mc, src, names, workdir):
        self.mc, self.names, self.sig = mc, [], {}
        gen = c_generator.CGenerator()
        cpu_scalars = []
        body = ['typedef unsigned __int128 u128; typedef struct fv fv;'] + [t if t.rstrip().endswith(';') else t + ';' for t in src.types
                                                      if not re.match(r'typedef\s+u128\s+u128', t) and not t.lstrip().startswith('struct fnvl_nframe;')]
        body.append('struct cpu cpu_storage; struct cpu *c = &cpu_storage;')
        order = [f for f in src.funcs if f in set(names)]
        body += [src.funcs[f].split('{')[0].strip() + ';' for f in order]
        for fn in order:
            body.append(src.funcs[fn])
        for n, ft in mc.structs['cpu']:
            if ft[0] in 'i' and ft[1] in (32, 64): cpu_scalars.append((n, ft[1]))
        self.cpu_fields = cpu_scalars
        body.append('void set_c(int i, u64 v){ switch(i){ ' + ' '.join('case %d: cpu_storage.%s = v; break;' % (i, n) for i, (n, b) in enumerate(cpu_scalars)) + ' } }')
        body.append('u64 get_c(int i){ switch(i){ ' + ' '.join('case %d: return cpu_storage.%s;' % (i, n) for i, (n, b) in enumerate(cpu_scalars)) + ' } return 0; }')
        for fn in names:
            try: body.append(self.wrapper(fn, gen))
            except ctrans.Unsupported: continue
            self.names.append(fn)
        path = os.path.join(workdir, 'h.c'); open(path, 'w').write('\n'.join(body) + '\n')
        so = os.path.join(workdir, 'h.so')
        p = subprocess.run(['/usr/bin/g++', '-x', 'c', '-O1', '-msse4.1', '-fno-trapping-math', '-ffp-contract=off', '-fno-fast-math', '-fPIC', '-shared', '-w', '-o', so, path, '-lm'], capture_output=True, text=True)
        if p.returncode: raise RuntimeError(p.stderr[-2000:])
        self.lib = ctypes.CDLL(so)

    def wrapper(self, fn, gen):
        mc = self.mc; fd = mc.funcs[fn]; ft = fd.decl.type
        lines, args, outs, sig = [], [], [], []
        for k, p in enumerate((ft.args.params if ft.args else [])):
            if p.name is None: continue
            t = mc.ctype(p.type); a = 'a%d' % k
            if t[0] == 'p':
                inner = p.type.type
                if not (isinstance(inner, c_ast.TypeDecl) and isinstance(inner.type, c_ast.IdentifierType) and t[1][0] in 'if'): raise ctrans.Unsupported('pointer to non-scalar')
                nm = ' '.join(inner.type.names)
                if t[1] == ctrans.I(8, 0):
                    lines.append('u8 %s[16]; for(int j=0;j<8;j++){ %s[j]=(in[p]>>(8*j)); %s[8+j]=(in[p+1]>>(8*j)); } p+=2;' % (a, a, a)); outs.append('for(int j=0;j<8;j++){ out[q]|=(u64)%s[j]<<(8*j); out[q+1]|=(u64)%s[8+j]<<(8*j);} q+=2;' % (a, a))
                    sig.append(('arr',)); args.append(a)
                else:
                    lines.append('%s %s = (%s)in[p++];' % (nm, a, nm)); outs.append('out[q++]=%s;' % a); sig.append(('cell', t[1])); args.append('&' + a)
            elif t[0] in 'if':
                nm = gen.visit(p.type.type).strip() if isinstance(p.type, c_ast.TypeDecl) else None
                if t[0] == 'f': lines.append('%s %s; { u64 w_ = in[p++]; __builtin_memcpy(&%s, &w_, sizeof %s); }' % (nm, a, a, a))        # a float is passed as its bit pattern
                else: lines.append('%s %s = (%s)in[p++];' % (nm, a, nm))
                args.append(a); sig.append(('s', t))
            elif t[0] == 's':
                tn = gen.visit(p.type.type)
                lines.append('%s %s; ' % (tn, a) + ' '.join('%s.%s=in[p++];' % (a, n) for n, _ in mc.structs[t[1]])); args.append(a); sig.append(('st', t))
            else: raise ctrans.Unsupported('param')
        rt = mc.ctype(ft.type)
        if rt == ('v',): call, res = '%s(%s);' % (fn, ','.join(args)), ''
        elif rt[0] == 'f': call, res = '%s r_ = %s(%s); u64 r = 0; __builtin_memcpy(&r, &r_, sizeof r_);' % ('double' if rt[1] == 64 else 'float', fn, ','.join(args)), 'out[q++]=r;'
        elif rt[0] == 'i': call, res = 'u64 r = (u64)%s(%s);' % (fn, ','.join(args)), 'out[q++]=r;'
        elif rt[0] == 's':
            tn = gen.visit(ft.type.type); call = '%s r = %s(%s);' % (tn, fn, ','.join(args)); res = ' '.join('out[q++]=r.%s;' % n for n, _ in mc.structs[rt[1]])
        else: raise ctrans.Unsupported('return')
        self.sig[fn] = (sig, rt)
        return 'void w_%s(const u64 *in, u64 *out){ int p=0,q=0; %s %s %s %s }' % (fn, ' '.join(lines), call, res, ' '.join(outs))

    def run(self, fn, words, cpu, rmode):
        """words: flattened argument words; cpu: {field: value}; rmode: Arm FPCR.RMode -> host rounding mode"""
        libm = ctypes.CDLL('libm.so.6')
        libm.fesetround({0: 0, 1: 0x800, 2: 0x400, 3: 0xC00}[rmode])
        self.lib.set_c.argtypes = [ctypes.c_int, ctypes.c_uint64]
        for i, (n, b) in enumerate(self.cpu_fields): self.lib.set_c(i, cpu.get(n, 0))
        w = (ctypes.c_uint64 * (len(words) + 1))(*words); o = (ctypes.c_uint64 * 64)()
        getattr(self.lib, 'w_' + fn)(w, o)
        libm.fesetround(0)
        return list(o)
