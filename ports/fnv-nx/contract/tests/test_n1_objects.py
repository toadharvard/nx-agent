#!/usr/bin/env python3
"""n1.check_objects: --msvc is the root of the pristine objects (meta.json, obj/u<i>.obj); a wrong root, a missing meta.json or a partial copy is an error,
never "every function has no MSVC object"."""
import json, os, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import n1

fails = []


def mk(d, n, declared):
    os.makedirs(d + '/obj')
    for i in range(n):
        open(d + '/obj/u%d.obj' % i, 'wb').close()
    open(d + '/obj/notes.txt', 'w').close()
    json.dump({'objects': declared}, open(d + '/meta.json', 'w'))


def err(msvc):
    try:
        n1.check_objects(msvc)
        return None
    except ValueError as e:
        return str(e)


with tempfile.TemporaryDirectory() as t:
    mk(t + '/ok', 3, 3)
    if err(t + '/ok') is not None:
        fails.append('a complete root was rejected: %s' % err(t + '/ok'))
    if n1.check_objects(t + '/ok') != 3:
        fails.append('count')
    if not (err(t + '/ok/obj') or '').startswith(t + '/ok/obj/obj is not a directory'):
        fails.append('the obj dir itself as --msvc (the old msvc/obj convention) is not rejected: %r' % err(t + '/ok/obj'))
    mk(t + '/part', 2, 3)
    if 'holds 2' not in (err(t + '/part') or ''):
        fails.append('partial copy: %r' % err(t + '/part'))
    mk(t + '/nometa', 1, 1)
    os.remove(t + '/nometa/meta.json')
    if 'meta.json' not in (err(t + '/nometa') or ''):
        fails.append('no meta.json: %r' % err(t + '/nometa'))
if fails:
    print('\n'.join('FAIL ' + f for f in fails))
    sys.exit(1)
print('n1 objects: ok')
