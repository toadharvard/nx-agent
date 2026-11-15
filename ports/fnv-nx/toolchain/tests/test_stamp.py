#!/usr/bin/env python3
"""Test of the compiler build key (toolchain/msvcmodel/stamp.py) and of the recipe's check of the pinned compiler tree (verify/recipe.py
compiler_stamp_errors, run at the first stage that compiles a unit):
  - build.sh computes the key by stamp.py only (no second computation) and the CLI and the function agree;
  - the key is a function of the content of the inputs alone (a copy of the inputs in another directory gives the same key; any changed input changes it);
  - a compiler tree whose MANIFEST.json build_key is stale, or which has no MANIFEST.json, is red; a tree with the current key is accepted;
  - a recipe build with the real pin: red naming compiler.build_key / MANIFEST.json exactly when the pinned tree's key is not this checkout's.
No network beyond the pin's own fetch, no game data.
Run: python3 -I <this file> from the checkout root."""
import copy
import hashlib
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


S1 = 'ab' * 32   # a stage-1 tree_sha256 (only its value enters K2)


def main():
    verify = os.path.join(TREE, 'verify')
    build_sh = open(os.path.join(TREE, 'toolchain', 'msvcmodel', 'build.sh')).read()
    stage2_sh = open(os.path.join(TREE, 'toolchain', 'msvcmodel', 'stage2.sh')).read()
    check('build.sh takes K1 from stamp.py and K2 from stamp.py over the stage-1 tree', re.search(r'^K1=\$\(python3 -I "\$HERE/stamp\.py" --k1', build_sh, re.M) is not None
          and re.search(r'^STAMP=\$\(python3 -I "\$HERE/stamp\.py" --stage1-tree "\$S1_TREE"', build_sh, re.M) is not None)
    check('build.sh has no second computation of the key (no sha256sum of the input list)', not re.search(r'sha256sum\s+"\$MSVC_PATCH"', build_sh))
    check('stage2.sh has no caller env flag files and no patch hash constants (patch paths come from stamp.py --files)',
          'FNVNX_TL_PLUGIN_FLAGS' not in stage2_sh and 'FNVNX_TL_PLUGIN_FLAGS' not in build_sh and not re.search(r'(X19|NOSIZED|NLGUEST)_PATCH_SHA256', stage2_sh)
          and '$PATCH_DIR' not in stage2_sh and '$PATCH_DIR' not in build_sh)
    check('every file stage2.sh applies or runs from toolchain/gcc is named through `listed` (stamp.py --files)',
          set(re.findall(r'listed (toolchain/\S+?)\)', stage2_sh)) == {i for i in stamp.STAGE2_INPUTS if i.startswith('toolchain/gcc/')})
    k1, k = stamp.stages(recipe=recipe, stage1_tree=S1)
    check('the keys are 16 hex digits', re.fullmatch('[0-9a-f]{16}', k) is not None and re.fullmatch('[0-9a-f]{16}', k1) is not None, (k1, k))
    run = lambda *a: subprocess.run([sys.executable, '-I', os.path.join(TREE, 'toolchain', 'msvcmodel', 'stamp.py')] + list(a), capture_output=True, text=True)
    cli = run('--stage1-tree', S1)
    check('the CLI gives the function\'s K2', cli.returncode == 0 and cli.stdout.strip() == k, cli.stderr)
    cli1 = run('--k1')
    check('the CLI --k1 gives K1, and K2 without a stage-1 tree is refused', cli1.stdout.strip() == k1 and run().returncode != 0)
    fl = run('--files')
    check('--files lists exactly STAGE2_INPUTS with their paths', fl.returncode == 0 and sorted(l.split()[0] for l in fl.stdout.splitlines()) == sorted(stamp.STAGE2_INPUTS), fl.stdout)
    check('an --override of a name that is no input is refused', run('--files', '--override', 'toolchain/gcc/other.patch=/dev/null').returncode != 0)
    check('K2 is over the stage-1 tree: another tree changes K2 and not K1', stamp.stages(recipe=recipe, stage1_tree='cd' * 32)[1] != k and stamp.stages(recipe=recipe, stage1_tree='cd' * 32)[0] == k1)
    td = tempfile.mkdtemp(prefix='test-stamp-')
    try:
        other = os.path.join(td, 'other-checkout')
        plug = stamp.plugin_inputs(recipe=recipe)
        check('the plugin file list is the recipe\'s own (every PLUGIN_SOURCES and PLUGIN_INPUTS file, nothing else)',
              plug == sorted({os.path.relpath(f, recipe.TREE) for f in list(recipe.PLUGIN_SOURCES.values()) + [x for v in recipe.PLUGIN_INPUTS.values() for x in v]})
              and any(n.endswith('decltype_spell.inc') for n in plug) and any(n.endswith('crossing_terms.inc') for n in plug))
        for i in list(stamp.INPUTS) + plug:
            os.makedirs(os.path.dirname(os.path.join(other, i)), exist_ok=True)
            shutil.copyfile(os.path.join(TREE, i), os.path.join(other, i))
        check('the key depends on content only (the inputs copied to another directory give the same key)', stamp.stamp(other, recipe=recipe, verify=verify, stage1_tree=S1) == k)
        n1, n2 = stamp.stages(other, recipe=recipe, verify=verify, stage1_tree=S1)
        check('the two keys: K2 is the build key', n2 == k and n1 == k1 and k1 != k)
        for i in stamp.INPUTS:
            with open(os.path.join(other, i), 'a') as f:
                f.write('\n')
            n1, n2 = stamp.stages(other, recipe=recipe, verify=verify, stage1_tree=S1)
            check('a changed input (%s) changes %s' % (i, 'K1 (and K2 through the new stage-1 tree)' if i in stamp.STAGE1_INPUTS else 'the key'), n1 != k1 if i in stamp.STAGE1_INPUTS else n2 != k)
            check('a stage-1 input does not enter K2 except through the stage-1 tree', i not in stamp.STAGE1_INPUTS or n2 == k)
            check('a changed %s input (%s) changes K1 exactly when it is a stage-1 input' % ('stage-1' if i in stamp.STAGE1_INPUTS else 'stage-2', i), (n1 != k1) == (i in stamp.STAGE1_INPUTS))
            shutil.copyfile(os.path.join(TREE, i), os.path.join(other, i))
        for i in plug:
            with open(os.path.join(other, i), 'a') as f:
                f.write('\n')
            n1, n2 = stamp.stages(other, recipe=recipe, verify=verify, stage1_tree=S1)
            check('a changed plugin file (%s) changes K2 and not K1 (the compiler is not rebuilt)' % i, n2 != k and n1 == k1)
            shutil.copyfile(os.path.join(TREE, i), os.path.join(other, i))
        # the plugin argv and the plugin stage's own inputs are in K2 (a recipe module with another value, K1 unchanged)
        import copy
        import types
        def with_recipe(**ch):
            m = types.ModuleType('recipe_changed')
            m.__dict__.update({n: v for n, v in recipe.__dict__.items() if not n.startswith('__')})
            for n, v in ch.items():
                m.__dict__[n] = v
            return m
        base_args = copy.deepcopy(recipe.PLUGIN_ARGS)
        for field, mut in (('args of fnvnx_order', lambda a: a['fnvnx_order'].update(args='ltr')), ('a new arg of fnvnx_sdd', lambda a: a['fnvnx_sdd'].update(x='1'))):
            a2 = copy.deepcopy(base_args)
            mut(a2)
            r2 = with_recipe(PLUGIN_ARGS=a2)
            r2.plugin_args = lambda origin, _r=r2: {n: dict(v, **({'mode': _r.origin_class(origin)} if n == 'fnvnx_ftoi' else {})) for n, v in _r.PLUGIN_ARGS.items()}
            m1, m2 = stamp.stages(recipe=r2, verify=verify, stage1_tree=S1)
            check('a changed PLUGIN_ARGS (%s) moves K2 and not K1' % field, m2 != k and m1 == k1)
        for field, val in (('PLUGIN_BUILD_FLAGS', recipe.PLUGIN_BUILD_FLAGS + ('-DX',)), ('PLUGIN_OUT', dict(recipe.PLUGIN_OUT, fnvnx_sdd='/out/x'))):
            m1, m2 = stamp.stages(recipe=with_recipe(**{field: val}), verify=verify, stage1_tree=S1)
            check('a changed %s of the recipe\'s plugin stage moves K2 and not K1' % field, m2 != k and m1 == k1, field)
        srcs = dict(recipe.PLUGIN_SOURCES)
        order = {n: srcs[n] for n in reversed(list(srcs))}
        m1, m2 = stamp.stages(recipe=with_recipe(PLUGIN_SOURCES=order), verify=verify, stage1_tree=S1)
        check('the plugin load order (PLUGIN_SOURCES order) is in K2', m2 != k)
        # the plugin flags stage2.sh uses: the recipe's order, its arguments, nothing from a caller
        so = os.path.join(td, 'x.so')
        open(so, 'wb').write(b'so')
        pj = os.path.join(td, 'plugins.json')
        json.dump({'plugins': [{'name': n, 'sha256': hashlib.sha256(b'so').hexdigest(), 'stage_key': 'k', 'path': so} for n in recipe.PLUGIN_SOURCES]}, open(pj, 'w'))
        for lang in stamp.LANGS:
            r = run('--plugin-flags', lang, '--plugins-json', pj)
            toks = r.stdout.split()
            check('--plugin-flags %s: %d -fplugin= flags in PLUGIN_SOURCES order, then the recipe\'s arguments' % (lang, len(recipe.PLUGIN_SOURCES)),
                  r.returncode == 0 and [t for t in toks if t.startswith('-fplugin=')] == ['-fplugin=' + so] * len(recipe.PLUGIN_SOURCES)
                  and [t for t in toks if t.startswith('-fplugin-arg-')] == stamp.plugin_argv(lang, recipe)
                  and any(t == '-fplugin-arg-fnvnx_ftoi-mode=native' for t in toks) and not any('-out=' in t for t in toks), r.stdout + r.stderr)
        json.dump({'plugins': [{'name': n, 'sha256': hashlib.sha256(b'so').hexdigest(), 'stage_key': 'k', 'path': so} for n in list(recipe.PLUGIN_SOURCES)[:-1]]}, open(pj, 'w'))
        check('a plugin list that is not exactly PLUGIN_SOURCES is refused', run('--plugin-flags', 'cxx', '--plugins-json', pj).returncode != 0)
        json.dump({'plugins': [{'name': n, 'sha256': '0' * 64, 'stage_key': 'k', 'path': so} for n in recipe.PLUGIN_SOURCES]}, open(pj, 'w'))
        check('a plugin file that is not the one its sha256 names is refused', run('--plugin-flags', 'cxx', '--plugins-json', pj).returncode != 0)
        # the values build.sh reads from the records: a changed runtime_configure / runtime_cflags / target / semantic flag / env pin changes the key
        vd = os.path.join(td, 'verify')
        os.makedirs(vd)
        for n in ('targets.json', 'guest_compiler.json', 'env.json'):
            shutil.copyfile(os.path.join(verify, n), os.path.join(vd, n))
        gc = json.load(open(os.path.join(vd, 'guest_compiler.json')))
        check('a verify dir copy with the same records gives the same key', stamp.stamp(verify=vd, recipe=recipe, stage1_tree=S1) == k)
        pinned = dict(gc, external=dict(gc['external'], commit='0' * 40))
        json.dump(pinned, open(os.path.join(vd, 'guest_compiler.json'), 'w'))
        check('a changed pin (the output of the build) does not change the key', stamp.stamp(verify=vd, recipe=recipe, stage1_tree=S1) == k)
        for field, val in (('runtime_configure', gc['runtime_configure'] + ['--disable-nls-test']), ('runtime_cflags', gc['runtime_cflags'] + ['-DX=1'])):
            json.dump(dict(gc, **{field: val}), open(os.path.join(vd, 'guest_compiler.json'), 'w'))
            try:
                kk = stamp.stamp(verify=vd, recipe=recipe, stage1_tree=S1)
            except Exception as ex:   # runtime_configure may refuse an uncited option: that is red as well, never the same key
                kk = 'refused: %s' % ex
            check('a changed %s changes the key (or is refused)' % field, kk != k, kk)
        json.dump(gc, open(os.path.join(vd, 'guest_compiler.json'), 'w'))
        tj = json.load(open(os.path.join(vd, 'targets.json')))
        tj['guest_platform']['triplet'] += 'x'
        json.dump(tj, open(os.path.join(vd, 'targets.json'), 'w'))
        check('a changed targets.json triplet moves K1 (stage 1 reads it; K2 follows through the stage-1 tree)', stamp.stages(verify=vd, recipe=recipe)[0] != k1)
        shutil.copyfile(os.path.join(verify, 'targets.json'), os.path.join(vd, 'targets.json'))
        # env.json: a changed pin of a package (the binutils package it copies, the host compiler) moves K1 and K2; the OS and architecture too
        ej = json.load(open(os.path.join(vd, 'env.json')))
        for pkg in ('binutils-aarch64-linux-gnu', [p for p in ej['debs']['packages'] if re.fullmatch(r'g\+\+-\d+-x86-64-linux-gnu', p)][0]):
            e2 = copy.deepcopy(ej)
            e2['debs']['packages'][pkg]['sha256'] = '0' * 64
            json.dump(e2, open(os.path.join(vd, 'env.json'), 'w'))
            m1, m2 = stamp.stages(verify=vd, recipe=recipe, stage1_tree=S1)
            check('a changed env.json pin (%s) moves K1 and K2' % pkg, m1 != k1 and m2 != k)
            e2 = copy.deepcopy(ej)
            e2['debs']['packages'][pkg]['version'] += '.1'
            json.dump(e2, open(os.path.join(vd, 'env.json'), 'w'))
            check('a changed env.json version (%s) moves K1' % pkg, stamp.stages(verify=vd, recipe=recipe)[0] != k1)
        for field, val in (('arch', 'aarch64'), ('os', {'id': 'debian', 'version_id': '12'})):
            json.dump(dict(ej, **{field: val}), open(os.path.join(vd, 'env.json'), 'w'))
            check('a changed env.json %s moves K1' % field, stamp.stages(verify=vd, recipe=recipe)[0] != k1)
        json.dump(ej, open(os.path.join(vd, 'env.json'), 'w'))
        check('--expect-package gives env.json\'s version', run('--expect-package', 'binutils-aarch64-linux-gnu', '--verify', verify).stdout.strip() == ej['debs']['packages']['binutils-aarch64-linux-gnu']['version'])
        check('an unpinned package is refused', run('--expect-package', 'no-such-package', '--verify', verify).returncode != 0)
        # the tree check, on trees written here
        tree = os.path.join(td, 'tree')
        os.makedirs(tree)
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('a compiler tree with no MANIFEST.json is red, naming this checkout\'s stage-1 key', len(e) == 1 and 'MANIFEST.json' in e[0] and k1 in e[0], e)
        man = lambda **c: json.dump({'compiler': c}, open(os.path.join(tree, 'MANIFEST.json'), 'w'))
        man(build_key='0' * 16)
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('a manifest with no stage-1 tree is red', len(e) == 1 and 'compiler_tree_sha256' in e[0], e)
        man(build_key='0' * 16, stage_keys={'compiler': '0' * 16, 'runtime': '0' * 16, 'compiler_tree_sha256': S1})
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('a stale stage-1 key is red, naming both', len(e) == 1 and '0' * 16 in e[0] and k1 in e[0] and 'stage-1 key' in e[0], e)
        man(build_key='0' * 16, stage_keys={'compiler': k1, 'runtime': '0' * 16, 'compiler_tree_sha256': S1})
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('a stale build_key is red, naming both keys and the stage-1 tree', len(e) == 1 and '0' * 16 in e[0] and k in e[0] and S1 in e[0] and 'build_key' in e[0], e)
        check('the message names the patch hash when the manifest\'s patch is another', 'msvcmodel_patch_sha256 is None' in e[0] and stamp.patch_sha256() in e[0], e)
        man(build_key='0' * 16, msvcmodel_patch_sha256=stamp.patch_sha256(), stage_keys={'compiler': k1, 'runtime': '0' * 16, 'compiler_tree_sha256': S1})
        e = recipe.compiler_stamp_errors(TREE, tree, 'cc')
        check('with the same patch and another key the message says a stage-2 input differs', len(e) == 1 and 'stage-2 input' in e[0], e)
        man(build_key=k, stage_keys={'compiler': k1, 'runtime': k, 'compiler_tree_sha256': S1})
        check('a compiler tree with this checkout\'s keys is accepted', recipe.compiler_stamp_errors(TREE, tree, 'cc') == [])
        man(build_key=k, stage_keys={'compiler': k1, 'runtime': k, 'compiler_tree_sha256': 'cd' * 32})
        check('the same build_key over another stage-1 tree is red (K2 is over the tree)', len(recipe.compiler_stamp_errors(TREE, tree, 'cc')) == 1)
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
