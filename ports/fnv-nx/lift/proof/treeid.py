"""The tree a result was made on, and the resume rule of a shard file.
Every record of shapeprove.py / simdshard.py carries `tree` = the commit of the running tree (`+dirty` when a tracked file differs from it).  Resume keeps only the complete records
of THIS tree that are not errors, and rewrites the file with them: a record of another tree (or without `tree`) and an error record are never resumed, so a fix of the prover is
always applied to every shape it could change."""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def current():
    def git(*a): return subprocess.run(['git', '-C', HERE] + list(a), check=True, capture_output=True, text=True).stdout.strip()
    return git('rev-parse', 'HEAD') + ('+dirty' if git('status', '--porcelain', '-uno') else '')


def resume(path, tree):
    """-> the set of shapes that stay done; the file keeps exactly their records, each once"""
    done, good = set(), []
    if os.path.exists(path):
        for l in open(path).read().split('\n'):
            try: r = json.loads(l)
            except ValueError: continue
            if r.get('tree') != tree or r.get('result') == 'error' or 'shape' not in r or r['shape'] in done: continue
            done.add(r['shape']); good.append(l)
        open(path, 'w').write(''.join(l + '\n' for l in good))
    return done
