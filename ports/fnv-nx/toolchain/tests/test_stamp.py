#!/usr/bin/env python3
"""Test of the compiler build key (toolchain/msvcmodel/stamp.py) and of the recipe's check of the pinned compiler tree (verify/recipe.py
compiler_stamp_errors, run at the first stage that compiles a unit):
  - build.sh computes the key by stamp.py only (no second computation) and the CLI and the function agree;
  - the key is a function of the content of the inputs alone (a copy of the inputs in another directory gives the same key; any changed input changes it);
  - a compiler tree whose MANIFEST.json build_key is stale, or which has no MANIFEST.json, is red; a tree with the current key is accepted;
  - a recipe build with the real pin: red naming compiler.build_key / MANIFEST.json exactly when the pinned tree's key is not this checkout's.
No network beyond the pin's own fetch, no game data.
Run: python3 -I <this file> from the checkout root."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

TREE = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(TREE, 'toolchain', 'msvcmodel'))
import stamp  # noqa: E402

recipe = pb.recipe
check = pb.Checks('test_stamp')


def main():
    build_sh = open(os.path.join(TREE, 'toolchain', 'msvcmodel', 'build.sh')).read()
    check('build.sh takes the key from stamp.py', re.search(r'^STAMP=\$\(python3 -I "\$HERE/stamp\.py"', build_sh, re.M) is not None)
    check('build.sh has no second computation of the key (no sha256sum of the input list)', not re.search(r'sha256sum\s+"\$MSVC_PATCH"', build_sh))
    k = stamp.stamp()
    cli = subprocess.run([sys.executable, '-I', os.path.join(TREE, 'toolchain', 'msvcmodel', 'stamp.py'),
                          '--patch', os.path.join(TREE, stamp.INPUTS[0]), '--build', os.path.join(TREE, stamp.INPUTS[1]),
                          '--patch-dir', os.path.join(TREE, 'toolchain', 'gcc')], capture_output=True, text=True)
    check('the key is 16 hex digits', re.fullmatch('[0-9a-f]{16}', k) is not None, k)
    check('the CLI with build.sh\'s arguments gives the function\'s key', cli.returncode == 0 and cli.stdout.strip() == k, cli.stderr)
    td = tempfile.mkdtemp(prefix='test-stamp-')
    try:
        other = os.path.join(td, 'other-checkout')
        for i in stamp.INPUTS:
            os.makedirs(os.path.dirname(os.path.join(other, i)), exist_ok=True)
            shutil.copyfile(os.path.join(TREE, i), os.path.join(other, i))
        check('the key depends on content only (the inputs copied to another directory give the same key)', stamp.stamp(other, recipe=recipe, verify=os.path.join(TREE, "verify")) == k)
        for i in stamp.INPUTS:
            with open(os.path.join(other, i), 'a') as f:
                f.write('\n')
            check('a changed input (%s) changes the key' % i, stamp.stamp(other, recipe=recipe, verify=os.path.join(TREE, "verify")) != k)
            shutil.copyfile(os.path.join(TREE, i), os.path.join(other, i))
        # the values build.sh reads from the records: a changed runtime_configure / runtime_cflags / target / semantic flag changes the key
        vd = os.path.join(td, 'verify')
        os.makedirs(vd)
        for n in ('targets.json', 'guest_compiler.json'):
            shutil.copyfile(os.path.join(TREE, 'verify', n), os.path.join(vd, n))
        gc = json.load(open(os.path.join(vd, 'guest_compiler.json')))
        check('a verify dir copy with the same records gives the same key', stamp.stamp(verify=vd, recipe=recipe) == k)
        pinned = dict(gc, external=dict(gc['external'], commit='0' * 40))
        json.dump(pinned, open(os.path.join(vd, 'guest_compiler.json'), 'w'))
        check('a changed pin (the output of the build) does not change the key', stamp.stamp(verify=vd, recipe=recipe) == k)
        for field, val in (('runtime_configure', gc['runtime_configure'] + ['--disable-nls-test']), ('runtime_cflags', gc['runtime_cflags'] + ['-DX=1'])):
            json.dump(dict(gc, **{field: val}), open(os.path.join(vd, 'guest_compiler.json'), 'w'))
            try:
                kk = stamp.stamp(verify=vd, recipe=recipe)
            except Exception as ex:   # runtime_configure may refuse an uncited option: that is red as well, never the same key
                kk = 'refused: %s' % ex
            check('a changed %s changes the key (or is refused)' % field, kk != k, kk)
        json.dump(gc, open(os.path.join(vd, 'guest_compiler.json'), 'w'))
        tj = json.load(open(os.path.join(vd, 'targets.json')))
        tj['guest_platform']['triplet'] += 'x'
        json.dump(tj, open(os.path.join(vd, 'targets.json'), 'w'))
        check('a changed targets.json triplet changes the key', stamp.stamp(verify=vd, recipe=recipe) != k)
        # the tree check, on trees written here
        tree = os.path.join(td, 'tree')
        os.makedirs(tree)
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('a compiler tree with no MANIFEST.json is red, naming this checkout\'s key', len(e) == 1 and 'MANIFEST.json' in e[0] and k in e[0], e)
        json.dump({'compiler': {'build_key': '0' * 16}}, open(os.path.join(tree, 'MANIFEST.json'), 'w'))
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('a compiler tree with a stale build_key is red, naming both keys', len(e) == 1 and '0' * 16 in e[0] and k in e[0] and 'build_key' in e[0], e)
        check('the message names the patch hash when the manifest\'s patch is another', 'msvcmodel_patch_sha256 is None' in e[0] and stamp.patch_sha256() in e[0], e)
        json.dump({'compiler': {'build_key': '0' * 16, 'msvcmodel_patch_sha256': stamp.patch_sha256()}}, open(os.path.join(tree, 'MANIFEST.json'), 'w'))
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('with the same patch and another key the message says another input differs', len(e) == 1 and 'another input of the build differs' in e[0], e)
        json.dump({'compiler': {'build_key': k}}, open(os.path.join(tree, 'MANIFEST.json'), 'w'))
        check('a compiler tree with this checkout\'s build_key is accepted', recipe.compiler_stamp_errors(TREE, tree, 'cc') == [])
        # the recipe, with the real pin
        src = os.path.join(td, 'u.cpp')
        open(src, 'w').write('int u() { return 0; }\n')
        r = pb.compile(src, os.path.join(td, 'u.o'), [], native=True)
        pin = json.load(open(os.path.join(pb.REAL, pb.T + 'verify/guest_compiler.json')))
        co = os.path.join(pb.STORE, 'external', pin['external']['commit'] + '.co')
        pinned = recipe.compiler_stamp_errors(TREE, co, pin['external']['name']) if os.path.isdir(co) else None
        if pinned is None:
            check('the pinned compiler tree was fetched by the build', False, r.stderr[-600:])
        elif pinned:
            check('recipe: the pinned tree has another key than this checkout, so a unit build is red naming the key', r.returncode != 0 and ('build_key' in r.stderr or 'MANIFEST.json' in r.stderr), r.stderr[-800:])
        else:
            check('recipe: the pinned tree has this checkout\'s key, so a unit builds', r.returncode == 0, r.stderr[-800:])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
