#!/usr/bin/env python3
"""The packager: the lifter's output for the whole code universe as linkable objects.
    liftall.py --shard K/N --out DIR [--jobs J]    lift + compile the entries whose index (address order) mod N == K  -> lift_K_of_N.a, manifest_K.tsv, missing_K.tsv, forms_K.tsv, undef_K.tsv
    liftall.py --finish --out DIR                  merge the shards -> manifest/missing/forms/undef.tsv, entries.tsv, disp.o (perfect-hash table, position independent), rt.a, gate_liftall.json
liftall.py --module NAME ...                  (any mode) a module of modules.tsv: its file, its assigned load base (FNVNX_BASE) and FNVNX_FACTS_OUT/<name>/universe.tsv
Deterministic: sorted iteration, no clocks in the outputs. Inputs FNVNX_EXE, FNVNX_UNIVERSE (universe.tsv), FNVNX_PROOF (certificates); outputs only under --out (exe-derived, never in the repository).
Prerequisites: python3 with capstone, numpy, pyelftools, unicorn (test/instest.py imports them); aarch64-linux-gnu-g++/gcc/binutils."""
import argparse, collections, glob, hashlib, json, multiprocessing as mp, os, re, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('bench', 'tools', 'test', ''):
    sys.path.insert(0, os.path.join(LIFT, p))
CC, CXX, NM, AR = 'aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++', 'aarch64-linux-gnu-nm', 'aarch64-linux-gnu-ar'
BATCH = 60
ENV = dict(os.environ, LC_ALL='C')
LIBC = {'memcpy', 'memset', 'memmove', 'memcmp', 'memchr', 'strlen', 'strcmp', 'strcpy', 'strncmp', 'strncpy', 'sqrt', 'sqrtf', 'floor', 'ceil', 'trunc', 'round', 'fabs', 'fmod', 'abort',
        '__aeabi_unwind_cpp_pr0', '__stack_chk_fail', 'ceilf', 'floorf', 'truncf', 'roundf', 'rint', 'rintf', 'sin', 'cos', 'atan2', 'exp', 'log', 'pow'}
CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c']   # plain AAPCS runtime files (as bench/chain.py)
G = {}


def flags():
    import instest
    return [f for f in instest.FLAGS if f not in ('-DFNVL_FLAGS_IO', '-DFNVL_STUBX')] + ['-I', LIFT + '/rt']


def run(cmd, **k):
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, **k)


def classify(s):
    return 'lifted' if re.fullmatch(r'L_[0-9a-f]{8}', s) else 'runtime' if s.startswith('fnvl_') else 'libc' if s in LIBC else 'other'


def rclass(t):
    return re.sub(r'\b[0-9a-f]{5,8}\b', 'X', t)


def entries(u):
    return sorted(u)


# ---------------------------------------------------------------- shard worker
def compile_text(text, o):
    open(o[:-2] + '.cpp', 'w').write(text)
    r = run([CXX] + G['flags'] + ['-x', 'c++', o[:-2] + '.cpp', '-o', o])
    os.remove(o[:-2] + '.cpp')
    return None if r.returncode == 0 else (re.sub(r"['‘][^'’]*['’]", 'N', (r.stderr.split('error:', 1) + [''])[1].strip().split('\n')[0]) or r.stderr.strip()[:80])


def lift_one(a):
    """-> (text, form counter) or (None, reason)"""
    import fnvlift as L
    import formcert
    G['cap'].clear()
    try:
        t, m = L.lift_entry(G['img'], a)
    except L.Unsupported as e:
        return None, str(e)
    t = re.sub(r'extern "C" void (L_%08x\(void\)\n\{)' % a, r'extern "C" __attribute__((section(".text.fnvl"))) void \1', t, count=1)
    assert '.text.fnvl' in t
    return t, formcert.census(G['cap'][-1][0])


def nm_parse(o):
    """-> ({defined symbol: size}, [undefined symbols])"""
    d, u = {}, []
    for l in run([NM, '-S', '-g', o]).stdout.splitlines():
        p = l.split()
        if len(p) == 2 and p[0] == 'U': u.append(p[1])
        elif len(p) == 4: d[p[3]] = int(p[1], 16)
    return d, sorted(u)


def work(job):
    bi, addrs, out = job
    o = '%s/obj/b_%08x.o' % (out, addrs[0]); rf = o[:-2] + '.json'
    if os.path.exists(rf) and os.path.exists(o): return json.load(open(rf))
    ent = G['ent']; texts, forms, missing = {}, {}, []
    for a in addrs:
        t, x = lift_one(a)
        if t is None: missing.append((a, ent[a][0], x))
        else: texts[a] = t; forms[a] = x
    pre = G['prelude']
    def comp(group, path): return compile_text(pre + '\n'.join(texts[a] for a in group), path)
    good = sorted(texts)
    if good and comp(good, o):
        bad = {}
        for a in good:
            e = comp([a], o)
            if e: bad[a] = e
        good = [a for a in good if a not in bad]
        for a in sorted(bad): missing.append((a, ent[a][0], 'nocompile: ' + bad[a])); del texts[a]; del forms[a]
        if good: comp(good, o)
    if not good: return dict(missing=sorted(missing), obj=None, syms={}, forms={}, undef=[])
    d, u = nm_parse(o)
    r = dict(missing=sorted(missing), obj=os.path.basename(o), syms={'%08x' % a: d['L_%08x' % a] for a in good}, undef=u,
             forms=dict(sum((forms[a] for a in good), collections.Counter())))
    json.dump(r, open(rf, 'w'), sort_keys=True)
    return r


def sampler(box):
    me = os.getpid()
    while not box.get('stop'):
        kids = subprocess.run(['ps', '-o', 'rss=', '--ppid', str(me)], capture_output=True, text=True).stdout.split()
        own = int(re.search(r'VmRSS:\s+(\d+)', open('/proc/self/status').read()).group(1))
        box['peak'] = max(box.get('peak', 0), own + sum(map(int, kids)))
        time.sleep(2)


def shard(a):
    import fnvlift as L, fnvlift.core as core, formcert
    t0 = time.time(); K, N = map(int, a.shard.split('/')); out = a.out
    for d in ('obj',): os.makedirs('%s/%s' % (out, d), exist_ok=True)
    ent = L.universe.load()[0]; ents = entries(ent); mine = ents[K::N]
    G.update(ent=ent, img=L.Image(a.exe), flags=flags(), prelude=L.PRELUDE, cap=[])
    orig = core.explore
    def cap(*x, **k): r = orig(*x, **k); G['cap'].append(r); return r
    core.explore = cap
    box = {}; threading.Thread(target=sampler, args=(box,), daemon=True).start()
    batches = [(i, mine[j:j + BATCH], out) for i, j in enumerate(range(0, len(mine), BATCH))]
    with mp.get_context('fork').Pool(a.jobs) as p: res = list(p.imap(work, batches, 1))
    box['stop'] = 1
    man = sorted((int(k, 16), v, r['obj']) for r in res for k, v in r['syms'].items())
    miss = sorted(m for r in res for m in r['missing'])
    certs = formcert.Certs(); forms = collections.Counter()
    for r in res: forms.update(r['forms'])
    undef = collections.Counter(s for r in res for s in r['undef'])
    objs = sorted(set(r['obj'] for r in res if r['obj']))
    w = lambda n, rows: open('%s/%s_%d.tsv' % (out, n, K), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    w('manifest', [('%08x' % ad, 'L_%08x' % ad, o, sz) for ad, sz, o in [(m[0], m[1], m[2]) for m in man]])
    w('missing', [('%08x' % ad, k, why) for ad, k, why in miss])
    w('forms', [(f, n, certs.status(f)) for f, n in sorted(forms.items())])
    w('undef', [(s, n, classify(s)) for s, n in sorted(undef.items())])
    arc = '%s/lift_%d_of_%d.a' % (out, K, N)
    if os.path.exists(arc): os.remove(arc)
    if objs: subprocess.check_call([AR, 'Dcr', arc] + [out + '/obj/' + o for o in objs])
    cert = sum(n for f, n in forms.items() if certs.status(f) == 'sail'); nc = sum(1 for f in forms if certs.status(f) == 'sail')
    top = collections.Counter(rclass(m[2]) for m in miss).most_common(5)
    print('shard %d/%d: entries %d lifted %d missing %d (top: %s) objects %d code bytes %d; forms %d (certified %d, uncertified %d; sites %d / %d); undefined non-lifted symbols %d; certificates dir %s' % (
        K, N, len(mine), len(man), len(miss), '; '.join('%d %s' % (n, r) for r, n in top), len(objs), sum(m[1] for m in man), len(forms), nc, len(forms) - nc, cert, sum(forms.values()) - cert,
        sum(1 for s in undef if classify(s) != 'lifted'), 'present' if certs.present else 'ABSENT (every form none)'))
    print('time %.0fs peak RSS (tree, sampled) %.0f MB' % (time.time() - t0, box.get('peak', 0) / 1024), file=sys.stderr)


# ---------------------------------------------------------------- finish
def tsv(f): return [l.rstrip('\n').split('\t') for l in open(f)] if os.path.exists(f) else []


def finish(a):
    import phf, fnvlift as L, formcert
    out = a.out; ks = sorted(int(re.search(r'manifest_(\d+)\.tsv', f).group(1)) for f in glob.glob(out + '/manifest_*.tsv'))
    ent = L.universe.load()[0]
    man = sorted(r for k in ks for r in tsv('%s/manifest_%d.tsv' % (out, k))); miss = sorted(r for k in ks for r in tsv('%s/missing_%d.tsv' % (out, k)))
    forms = collections.defaultdict(lambda: [0, 'none']); undef = collections.defaultdict(lambda: [0, ''])
    for k in ks:
        for f, n, s in tsv('%s/forms_%d.tsv' % (out, k)): forms[f][0] += int(n); forms[f][1] = s
        for s, n, c in tsv('%s/undef_%d.tsv' % (out, k)): undef[s][0] += int(n); undef[s][1] = c
    w = lambda n, rows: open('%s/%s.tsv' % (out, n), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    w('manifest', man); w('missing', miss); w('forms', [(f, v[0], v[1]) for f, v in sorted(forms.items())]); w('undef', [(s, v[0], v[1]) for s, v in sorted(undef.items())])
    syms = {int(r[0], 16): r[1] for r in man}
    code = dict(syms)
    imp = []
    # runtime: plain AAPCS files without -ffixed, guest-register files with the lifted-code flags
    rd = out + '/rt'; os.makedirs(rd, exist_ok=True)
    jobs = [([CC] + CF, 'rt.c'), ([CC] + CF + ['-I', LIFT + '/rt'], 'x87.c'), ([CC] + CF, 'cpumodel.c'), ([CXX] + flags() + ['-x', 'c++'], 'seh.c'), ([CXX] + flags() + ['-x', 'c++'], 'enter.c'), ([CXX] + flags() + ['-x', 'c++'], 'unhandled.c'), ([CXX] + flags() + ['-x', 'c++'], 'msvcrt_eh.c'), ([CXX] + flags() + ['-x', 'c++'], 'msvcrt_fp.c'), ([CXX] + flags() + ['-x', 'c++'], 'msvcrt_ci.c')]
    jobs += [([CC] + CF + ['-fno-builtin'], f) for f in ('fmath.c', 'rcptab.c') if os.path.exists('%s/rt/%s' % (LIFT, f))]
    for cmd, f in jobs: subprocess.check_call(cmd + ['%s/rt/%s' % (LIFT, f), '-o', '%s/%s.o' % (rd, f[:-2])])
    rtdef = set(l.split()[-1] for _, f in jobs for l in run([NM, '-g', '--defined-only', '%s/%s.o' % (rd, f[:-2])]).stdout.splitlines() if l.split())
    if a.bridge:           # import entries (tools/impgen.py): keys of the bridge table join the dispatcher's domain
        import impgen
        imp, blocked, hit, absent = impgen.generate(a.bridge, out, a.need, a.l3, rtdef)
        if hit or absent: sys.exit('finish: an import row a loaded image needs has no entry (imp_blocked.tsv)')
        assert not set(k for k, *_ in imp) & set(syms)
        syms.update({k: s for k, s, *_ in imp})
    for k, s in ((0x000fffd0, 'fnvl_msvcrt_eh_nested_catch'), (0x000fffe0, 'fnvl_msvcrt_eh_nested_collide')):     # the two handlers the msvcrt EH runtime registers on fs:[0] (rt/msvcrt.h FNVL_EH_KEY_*): guest entries with keys of their own
        if s in rtdef: syms[k] = s
    h = phf.build(list(syms)); open(out + '/disp.S', 'w').write(phf.table_asm(syms, h))
    w('entries', [('%08x' % x, s, 'code' if x in code else 'import') for x, s in sorted(syms.items())])
    if imp: open(out + '/entries.tsv', 'a').write(open(out + '/imp_entries.tsv').readline())      # `# table_id <sha256 of bridge.tsv>` (last line)
    subprocess.check_call([CC, '-mabi=ilp32', '-fPIC', '-c', out + '/disp.S', '-o', out + '/disp.o'])
    if imp:
        subprocess.check_call([CXX] + flags() + ['-x', 'c++', out + '/imp.cpp', '-o', rd + '/imp.o']); jobs.append((None, 'imp.c'))
    if os.path.exists(out + '/rt.a'): os.remove(out + '/rt.a')
    sf = sorted(l.split()[-1] for l in run([NM, '-g', '--defined-only', rd + '/x87.o']).stdout.splitlines() if l.split())      # the 80-bit integer-only core: the SoftFloat class of the instruction counter (L4)
    open(out + '/softfloat.tsv', 'w').write(''.join(x + '\n' for x in sf))
    subprocess.check_call([AR, 'Dcr', out + '/rt.a'] + ['%s/%s.o' % (rd, f[:-2]) for _, f in jobs])
    nobody = len(ent) - len(man)
    cert = sum(v[0] for v in forms.values() if v[1] == 'sail'); tot = sum(v[0] for v in forms.values()); nc = sum(1 for v in forms.values() if v[1] == 'sail')
    reasons = collections.Counter(rclass(r[2]) for r in miss).most_common(5)
    bytes_ = sum(int(r[3]) for r in man)
    res = dict(schema='fnvnx-gate/1', name='l3.liftall', status='green' if len(man) == len(ent) else 'red', universe=len(ent), certified=len(man), uncertified=len(ent) - len(man), imports=len(imp),
               build='measurement: forms without a certificate %d of %d' % (len(forms) - nc, len(forms)))
    json.dump(res, open(out + '/gate_liftall.json', 'w'), sort_keys=True)
    print('finish: shards %s; universe %d, body %d, missing %d (top: %s), %d entries without a body; objects %d, code bytes %d; forms %d (certified %d, uncertified %d; sites %d / %d); undefined non-lifted symbols %d; dispatcher %d slots, %d hash attempts; %s' % (
        ks, len(ent), len(man), len(miss), '; '.join('%d %s' % (n, r) for r, n in reasons), nobody, len(set(r[2] for r in man)), bytes_, len(forms), nc, len(forms) - nc, cert, tot - cert,
        sum(1 for s, v in undef.items() if v[1] != 'lifted'), len(h['slots']), h['attempts'], res['status']))
    if res['status'] != 'green': sys.exit('finish: %d universe entries have no body (missing.tsv): the pack is not complete' % (len(ent) - len(man)))


def module_env(name):
    """--module NAME: the file, the assigned load base and the universe of a modules.tsv module, as the environment the lifter tools read (FNVNX_EXE, FNVNX_BASE, FNVNX_UNIVERSE)."""
    import pemod
    path, row = pemod.locate(name); u = '%s/%s/universe.tsv' % (os.environ.get('FNVNX_FACTS_OUT', '/home/user/l3facts2'), os.path.splitext(row.name)[0])
    if not os.path.exists(u): sys.exit('--module %s: no universe %s (tools/universe.py --module)' % (name, u))
    os.environ.update(FNVNX_EXE=path, FNVNX_BASE='%08x' % row.base, FNVNX_UNIVERSE=u)
    return path


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--shard'); A('--finish', action='store_true'); A('--out', default='/home/user/l3work/liftall'); A('--jobs', type=int, default=3)
    A('--exe', default=os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); A('--module', help='a module of modules.tsv (lifted at its assigned base, with its universe)')
    A('--bridge', default=os.environ.get('FNVNX_BRIDGE')); A('--need', action='append', default=[]); A('--l3', default=None)     # --finish: import entries
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    if a.module: a.exe = module_env(a.module)
    if a.finish: finish(a)
    elif a.shard: shard(a)
    else: ap.error('--shard K/N or --finish')


if __name__ == '__main__': main()
