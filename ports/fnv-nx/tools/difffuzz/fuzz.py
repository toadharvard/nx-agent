"""fuzz.py - coverage-guided differential fuzzing over a list of functions (resumable, incremental).

Per function: inputs are the logical scalar arguments, the contents of every argument buffer (pointer / this /
by-reference aggregate), the data-window globals the oracle code refers to (minus pinned literals), the canned
results of intercepted calls and the background mode. Coverage is the union of basic blocks reached on both
machines; a case that reaches a new block joins the corpus and is mutated further.

On a divergence the case is minimised (fewer overlays, zero bytes, zero background) while the divergence kind stays
the same, probed for a root-cause hint (classify.py) and saved as <out>/div/<addr>-<n>.json with the minimal case;
`difffuzz.py repro` replays it. Results go to <out>/results.jsonl, one line per function, written as soon as the
function is done, so a campaign can be stopped and resumed (--resume skips functions already there).
"""
import json
import multiprocessing as mp
import os
import random
import struct
import time
import traceback

import config
import world

INT_EDGES = [0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 33, 63, 64, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff,
             0x10000, 0x7fffffff, 0x80000000, 0xffffffff, 0xfffffffe, 0xffffff80, 0xffff8000]
FLT_EDGES = [0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.0, 1e-7, 1e10, -1e10, 3e9, -3e9, 2147483648.0, 4294967296.0, 65536.5,
             0.49999997, 1.5, 2.5, -2.5, 1e-40, 3.4e38, float('inf'), float('-inf'), float('nan'), 100.0, 0.1]
BUF_LEN = 512
GLOBAL_LEN = 64


def _rand_scalar(r, v):
    if v.is_bool:
        return bytes([r.getrandbits(1)])
    if v.is_float:
        x = r.choice(FLT_EDGES) if r.random() < 0.6 else r.uniform(-1e6, 1e6) * (10 ** r.randint(-6, 3))
        return struct.pack('<f' if v.size == 4 else '<d', x)
    n = r.choice(INT_EDGES) if r.random() < 0.6 else r.getrandbits(8 * min(v.size, 8))
    if r.random() < 0.2:
        n = (-n) & ((1 << (8 * v.size)) - 1)
    return (n & ((1 << (8 * v.size)) - 1)).to_bytes(v.size, 'little')


def _pointer_word(r, bufs):
    if bufs and r.random() < 0.7:
        return bufs[r.randrange(len(bufs))] + 4 * r.randrange(BUF_LEN // 8)
    return r.choice([0, world.WINDOW[0] + 0x1000 * r.randrange(0x1100), world.HEAP + 0x1000 * r.randrange(64)])


def _rand_buffer(r, bufs, n=BUF_LEN):
    mode = r.random()
    if mode < 0.25:
        return bytes(n)
    if mode < 0.5:
        return r.randbytes(n)
    out = bytearray(n)
    for i in range(0, n, 4):
        c = r.random()
        if c < 0.35:
            w = r.choice(INT_EDGES)
        elif c < 0.6:
            w = _pointer_word(r, bufs)
        elif c < 0.8:
            w = struct.unpack('<I', struct.pack('<f', r.choice(FLT_EDGES[:20])))[0]
        else:
            w = r.getrandbits(32)
        out[i:i + 4] = struct.pack('<I', w & 0xffffffff)
    return bytes(out)


class Gen:
    def __init__(self, target, globals_, seed):
        self.t = target
        self.r = random.Random(seed)
        self.bufs = [a for _, a in target.buffers()]
        self.globals = globals_

    def fresh(self):
        r = self.r
        c = world.Case(seed=r.getrandbits(31), bg='zero' if r.random() < 0.8 else 'rand')
        for v in self.t.conv.vals:
            if v.label in dict(self.t.buffers()) and (v.is_ptr or v.label == 'this'):
                continue
            c.args[v.label] = _rand_scalar(r, v) if v.kind in ('gpr', 'fpr') else r.randbytes(v.size)
        for lab, a in self.t.buffers():
            c.mem[a] = _rand_buffer(r, self.bufs)
        for g in self.globals:
            if r.random() < 0.7:
                c.mem[g] = _rand_buffer(r, self.bufs, GLOBAL_LEN)
        c.rets = [_rets(r, self.bufs) for _ in range(8)]
        return c

    def mutate(self, c):
        r = self.r
        c = c.copy()
        for _ in range(r.randint(1, 4)):
            what = r.random()
            if what < 0.3 and c.args:
                lab = r.choice(list(c.args))
                v = next(x for x in self.t.conv.vals if x.label == lab)
                if v.kind in ('gpr', 'fpr'):
                    c.args[lab] = _rand_scalar(r, v)
                else:
                    b = bytearray(c.args[lab]); i = r.randrange(len(b)) if b else 0
                    if b:
                        b[i] = r.getrandbits(8)
                    c.args[lab] = bytes(b)
            elif what < 0.8 and c.mem:
                a = r.choice(list(c.mem))
                b = bytearray(c.mem[a])
                op = r.random()
                i = r.randrange(0, len(b) - 3, 4) if len(b) >= 4 else 0
                if op < 0.3:
                    b[r.randrange(len(b))] ^= 1 << r.randrange(8)
                elif op < 0.6:
                    b[i:i + 4] = struct.pack('<I', r.choice(INT_EDGES))
                elif op < 0.8:
                    b[i:i + 4] = struct.pack('<I', _pointer_word(r, self.bufs) & 0xffffffff)
                else:
                    b[i:i + 4] = struct.pack('<f', r.choice(FLT_EDGES))
                c.mem[a] = bytes(b)
            elif what < 0.9:
                k = r.randrange(8)
                while len(c.rets) <= k:
                    c.rets.append(None)
                c.rets[k] = _rets(r, self.bufs)
            else:
                c.seed = r.getrandbits(31)
                c.bg = 'zero' if r.random() < 0.7 else 'rand'
        return c


def _rets(r, bufs):
    c = r.random()
    if c < 0.3:
        w = r.choice([0, 1, 0xffffffff])
    elif c < 0.6:
        w = _pointer_word(r, bufs)
    else:
        w = r.getrandbits(32)
    return struct.pack('<II', w & 0xffffffff, r.getrandbits(32)) + struct.pack('<d', r.choice(FLT_EDGES))


def minimise(pair, case, kind, max_steps, budget=120):
    """Greedy reduction keeping the divergence kind."""
    runs = [0]

    def same(c):
        if runs[0] >= budget:
            return False
        runs[0] += 1
        _, _, d = pair.run(c, max_steps)
        return d is not None and d['kind'] == kind

    best = case.copy()
    trial = best.copy(); trial.bg = 'zero'
    if best.bg != 'zero' and same(trial):
        best = trial
    trial = best.copy(); trial.rets = [b'\0' * 16] * len(best.rets)
    if same(trial):
        best = trial
    for a in list(best.mem):
        trial = best.copy(); del trial.mem[a]
        if same(trial):
            best = trial
    for lab in list(best.args):
        trial = best.copy(); trial.args[lab] = bytes(len(best.args[lab]))
        if best.args[lab] != trial.args[lab] and same(trial):
            best = trial
    for a in list(best.mem):
        b = best.mem[a]
        step = len(b) // 2
        while step >= 4:
            changed = False
            for lo in range(0, len(b), step):
                nb = b[:lo] + bytes(min(step, len(b) - lo)) + b[lo + step:]
                if nb == b:
                    continue
                trial = best.copy(); trial.mem[a] = nb
                if same(trial):
                    best, b, changed = trial, nb, True
            if not changed:
                step //= 2
        # trim trailing zeros
        nb = b.rstrip(b'\0')
        nb = nb.ljust((len(nb) + 3) // 4 * 4, b'\0') if nb else b''
        if nb != b:
            trial = best.copy()
            if nb:
                trial.mem[a] = nb
            else:
                del trial.mem[a]
            if same(trial):
                best = trial
    while best.rets and best.rets[-1] in (None, b'\0' * 16):
        trial = best.copy(); trial.rets = best.rets[:-1]
        if trial.rets == best.rets:
            break
        best = trial
    return best, runs[0]


_UC = None


def unit_cache():
    global _UC
    if _UC is None:
        import engine
        _UC = engine.UnitCache(str(config.LINK), str(config.A64WORK))
    return _UC


def fuzz_one(item, opts):
    import classify
    src, addr = item['source'], int(item['address'], 16)
    out = dict(source=src, address=item['address'], symbol=item.get('symbol'), unit=item.get('unit'),
               size=item.get('size'), kind=item.get('kind'), execs=0, divergences=[], status='ok')
    t0 = time.time()
    try:
        uc = unit_cache()
        pair, target = uc.pair(src, addr)
    except Exception as e:  # noqa: BLE001
        out['status'] = 'setup:' + type(e).__name__
        out['error'] = str(e)[:300]
        return out
    out['conv'] = target.conv.describe()
    fn = uc.x86_unit(src, addr).funcs[addr]
    pinned = set(pair.pinned)
    globals_ = sorted({tg for _, k, tg, _ in fn['relocs'] if tg is not None and world.WINDOW[0] <= tg < world.WINDOW[1]
                       and tg not in pinned})
    gen = Gen(target, globals_, addr)
    corpus, cov, sigs = [], set(), {}
    iters, tlimit = opts['iters'], opts['time']
    zero = world.Case(seed=1)
    queue = [zero] + [gen.fresh() for _ in range(3)]
    i = 0
    while i < iters and (not tlimit or time.time() - t0 < tlimit):
        case = queue.pop() if queue else (gen.mutate(random.Random(i).choice(corpus)) if corpus and
                                          gen.r.random() < 0.8 else gen.fresh())
        i += 1
        try:
            rx, ra, div = pair.run(case, opts['max_steps'])
        except Exception as e:  # noqa: BLE001
            out['status'] = 'run:' + type(e).__name__
            out['error'] = (str(e) + traceback.format_exc()[-400:])[:800]
            break
        out['execs'] += 1
        new = {('x', b) for b in rx.blocks} | {('a', b) for b in ra.blocks}
        if not new <= cov:
            cov |= new
            corpus.append(case)
        if rx.error == 'timeout' or ra.error == 'timeout':
            out['timeouts'] = out.get('timeouts', 0) + 1
        if div is None:
            continue
        sig = div['kind']
        if sig in sigs:
            continue
        mcase, mruns = minimise(pair, case, sig, opts['max_steps'])
        out['execs'] += mruns
        _, _, mdiv = pair.run(mcase, opts['max_steps'])
        mdiv = mdiv or div
        hint = classify.hint(pair, target, mcase, mdiv, fn, opts['max_steps'])
        out['execs'] += hint.pop('_runs', 0)
        rec = dict(source=src, address=item['address'], symbol=item.get('symbol'), conv=out['conv'], divergence=mdiv,
                   hint=hint, case=mcase.to_json(), found_at=i)
        os.makedirs(os.path.join(opts['out'], 'div'), exist_ok=True)
        path = os.path.join(opts['out'], 'div', '%s-%d.json' % (item['address'], len(sigs)))
        json.dump(rec, open(path, 'w'), indent=1)
        sigs[sig] = path
        out['divergences'].append(dict(kind=sig, cause=hint.get('cause'), file=os.path.basename(path),
                                       soft=bool(mdiv.get('soft'))))
        if len(sigs) >= 3:
            break
    out['coverage'] = len(cov)
    out['corpus'] = len(corpus)
    out['seconds'] = round(time.time() - t0, 2)
    return out


def _worker(args):
    item, opts = args
    try:
        return fuzz_one(item, opts)
    except Exception as e:  # noqa: BLE001
        return dict(source=item['source'], address=item['address'], status='crash:' + type(e).__name__,
                    error=traceback.format_exc()[-600:], execs=0, divergences=[])


def campaign(a):
    os.makedirs(a.out, exist_ok=True)
    res_path = os.path.join(a.out, 'results.jsonl')
    done = set()
    if a.resume and os.path.exists(res_path):
        for line in open(res_path):
            try:
                done.add(json.loads(line)['address'])
            except Exception:  # noqa: BLE001
                pass
    items = []
    for line in open(a.targets):
        line = line.strip()
        if line and not line.startswith('#'):
            it = json.loads(line)
            if it['address'] not in done:
                items.append(it)
    opts = dict(iters=a.iters, time=a.time, max_steps=a.max_steps, out=a.out)
    print('%d functions to fuzz (%d already done)' % (len(items), len(done)), flush=True)
    n = 0
    with open(res_path, 'a') as res:
        if a.jobs <= 1:
            it = map(_worker, ((x, opts) for x in items))
        else:
            pool = mp.get_context('fork').Pool(a.jobs, maxtasksperchild=50)
            it = pool.imap_unordered(_worker, ((x, opts) for x in items), chunksize=1)
        for r in it:
            res.write(json.dumps(r) + '\n')
            res.flush()
            n += 1
            if r.get('divergences'):
                print('DIV %s %s %s' % (r['address'], r['source'], [d['kind'] + '/' + str(d['cause'])
                                                                    for d in r['divergences']]), flush=True)
            if n % 25 == 0:
                print('%d/%d done' % (n, len(items)), flush=True)
