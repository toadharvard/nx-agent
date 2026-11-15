#!/usr/bin/env python3
"""tally.py FILE...: sums bytes of emitcat_K.txt files (liftgen --emitcat) per emit category and per emitter. Reads only what the tags carry (emitter, category, fragment bytes) and the bytes between tags."""
import re, sys, collections, json
PREFIX = b'//@e '
cat = collections.Counter(); emi = collections.Counter(); cnt = collections.Counter(); frag = collections.Counter()
total = labels = recs = 0
for path in sys.argv[1:]:
    d = open(path, 'rb').read(); i = 0
    while i < len(d):
        j = d.index(b'\n', i); m = re.fullmatch(rb'//@ ([0-9a-f]{8}) (\d+)', d[i:j]); n = int(m[2]); i = j + 1; recs += 1
        left = n; cur = None; pre = 0; elems = []
        while left > 0:
            j = d.index(b'\n', i) + 1; ln = d[i:j]; i = j
            if ln.startswith(PREFIX):
                f = ln[len(PREFIX):].decode().split()
                cur = [f[0], f[1], 0, {x.split(':')[0]: int(x.split(':')[1]) for x in f[3:]}, 0, 0]; elems.append(cur); continue
            left -= len(ln)
            if cur is None: pre += len(ln)
            else:
                cur[2] += len(ln)
                if re.match(rb'L_[0-9a-f]{8}: ;', ln) and cur[0] == 'core.lift2': cur[4] += 1; cur[5] += len(ln)
        # the closing "}\n" line belongs to the function, not to the last element
        if elems: elems[-1][2] -= 2; pre += 2
        total += n; cat['boilerplate(other)'] += pre; emi['<function header, decls, closing brace>'] += pre
        for key, c, nb, fr, lab, lb in elems:
            fb = sum(fr.values()); fb = min(fb, nb)
            for fc, fbytes in fr.items(): cat[fc] += fbytes; emi['<fragment:%s>' % fc] += fbytes; frag[fc] += fbytes
            cat[c] += nb - fb; emi[key] += nb - fb - lb; emi['core.lift2 (instruction label lines)'] += lb; cnt[key] += 1; labels += lab
print(json.dumps(dict(records=recs, total_bytes=total, labels=labels, cat=dict(cat), emitters=dict(emi), counts=dict(cnt)), sort_keys=True))
