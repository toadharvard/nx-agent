"""The lifter's output is a pure function of the unit: the same text in every process (the children run with -P -s and a PYTHONHASHSEED of their own, since -I would ignore it), whatever was lifted before it (no node serial, id() or hash order reaches the emitter).
Universe by rule: every one-instruction unit [opcode map (one byte, 0F), opcode byte, ModRM byte, four zero bytes, RET] of the synthetic image Image.from_bytes at 0x401000 (the buffers of test_sites).
A unit's text is its lift (or 'UNSUPPORTED reason').  Process A lifts the universe in sorted order, process B (another PYTHONHASHSEED) in a seeded shuffle after an unrelated batch;
FNVNX_LIFT_STRIDE (default 97) takes every STRIDE-th unit by sorted position, 1 is the whole universe.  Further, COLD units are lifted one per fresh process and compared with A.
    python3 -I test_lift_canonical.py --cpufeat PATH"""
import hashlib, os, random, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); import _inputs
CF = _inputs.cpufeat_arg() if '--child' not in sys.argv else None
STRIDE = int(os.environ.get('FNVNX_LIFT_STRIDE', '97')); COLD = int(os.environ.get('FNVNX_LIFT_COLD', '6'))


def universe():
    return [(m, o, r) for m in (0, 1) for o in range(256) for r in range(256)]


def code(u):
    m, o, r = u
    return bytes(([0x0f] if m else []) + [o, r, 0, 0, 0, 0, 0xc3])


_DONE = []
def lift_units(units, cpufeat):
    if not _DONE: _inputs.configure(cpufeat); _DONE.append(1)
    import fnvlift as L
    from fnvlift.image import Image
    out = {}
    for u in units:
        try: t = L.lift(Image.from_bytes(code(u), 0x401000), 0x401000, len(code(u)))[0]
        except Exception as e: t = 'RAISED %s %s' % (type(e).__name__, e)      # a unit without a lift raises, deterministically
        out[u] = hashlib.sha256(t.encode()).hexdigest()
    return out


def child(mode, outp, cpufeat):
    U = universe()[::STRIDE]
    if mode == 'shuffled':
        rest = [u for u in universe() if u not in set(U)]; random.Random(7).shuffle(rest)
        lift_units(rest[::5][:3000], cpufeat)                       # an unrelated batch first
        random.Random(11).shuffle(U)
    elif mode.startswith('cold'): U = [U[int(mode[4:])]]
    res = lift_units(U, cpufeat)
    with open(outp, 'w') as f:
        for u, h in sorted(res.items()): f.write('%d %d %d %s\n' % (*u, h))


def run(mode, seed):
    p = tempfile.mktemp(prefix='liftcanon-')
    subprocess.run([sys.executable, '-P', '-s', os.path.abspath(__file__), '--child', mode, p, CF], check=True, env={**os.environ, 'PYTHONHASHSEED': seed})
    return dict((tuple(map(int, l.split()[:3])), l.split()[3]) for l in open(p))


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--child':
        child(sys.argv[2], sys.argv[3], sys.argv[4]); sys.exit(0)
    a = run('sorted', '1'); b = run('shuffled', '2')
    diff = [u for u in a if a[u] != b.get(u)]
    n = len(universe()[::STRIDE]); cold = 0; cdiff = []
    for i in range(COLD):
        k = i * (n // max(COLD, 1)); c = run('cold%d' % k, str(10 + i)); (u, h), = c.items(); cold += 1
        if a[u] != h: cdiff.append(u)
    print('units %d, identical %d, differing %d %s; cold single-process units %d, differing %d %s' % (len(a), len(a) - len(diff), len(diff), diff[:10], cold, len(cdiff), cdiff[:10]))
    sys.exit(0 if a and not diff and not cdiff and set(a) == set(b) else 1)
