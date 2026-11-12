#!/usr/bin/env python3
"""Tests for tools/gates/ready.py, the first-launch readiness scorecard (R0-gates, docs/READINESS.md).

    python3 -I tests/gates/test_ready.py [-v]

fixtures/ready is an artifacts repository laid out as <branch>/<path> in which every gate passes. Its publish
order is in _published.json and its commit distances in _commits.json; _render/report.json stands in for the
render branch. fixtures/port holds the checkout files ready.py reads (placement table, NPDM, PATCHES.md, the S12
classifier, the files whose content closes an S12 class) and the thresholds with small shard counts. Each test
copies the world, changes one thing and reads the JSON scorecard. The fixtures hold numbers and made-up names
only; no game data.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'gates'))
import ready  # noqa: E402
from ready import readf, writef  # noqa: E402

FIX = os.path.join(HERE, 'fixtures')
C = 'c0ffee000001'


class World:
    """a scratch copy of fixtures/ready and fixtures/port"""

    def __init__(self, tmp):
        self.w = os.path.join(tmp, 'ready')
        self.p = os.path.join(tmp, 'port')
        shutil.copytree(os.path.join(FIX, 'ready'), self.w)
        shutil.copytree(os.path.join(FIX, 'port'), self.p)
        self.tmp = tmp

    def path(self, rel, port=False):
        return os.path.join(self.p if port else self.w, rel)

    def read(self, rel, port=False):
        return readf(self.path(rel, port))

    def write(self, rel, text, port=False, published=None):
        p = self.path(rel, port)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        writef(p, text if isinstance(text, str) else json.dumps(text))
        if published is not None:
            self.edit_json('_published.json', lambda d: d.__setitem__(rel, published))

    def sub(self, rel, old, new, port=False):
        t = self.read(rel, port)
        assert old in t, (rel, old)
        self.write(rel, t.replace(old, new), port)

    def remove(self, rel, port=False):
        os.remove(self.path(rel, port))

    def edit_json(self, rel, fn, port=False):
        d = json.loads(self.read(rel, port))
        fn(d)                                   # changes d in place
        self.write(rel, d, port)

    def latest(self):
        return max(json.loads(self.read('_published.json')).values())

    def run(self, *extra):
        out = os.path.join(self.tmp, 'card.json')
        md = os.path.join(self.tmp, 'card.md')
        rc = ready.main(['--fixtures', self.w, '--port', self.p, '--thresholds', self.path('thresholds.json', True),
                         '--head', C, '--json', out, '--md', md, *extra])
        assert rc == 0
        card = json.loads(readf(out))
        card['by_id'] = {g['id']: g for g in card['gates']}
        card['md'] = readf(md)
        return card


class ReadyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.world = World(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def gate(self, card, gid):
        self.assertIn(gid, card['by_id'], sorted(card['by_id']))
        return card['by_id'][gid]

    # ---- the whole card
    def test_all_pass_is_ready(self):
        card = self.world.run()
        self.assertEqual(card['verdict'], 'READY', [(g['id'], g['status']) for g in card['gates']
                                                    if g['status'] != 'pass' and g['required']])
        self.assertEqual(card['blockers'], [])
        self.assertEqual(card['missing'], [])
        self.assertEqual(card['counts']['fail'], 0)
        self.assertIn('**READY**', card['md'])
        for g in card['gates']:
            for s in g['sources']:
                self.assertIn('behind', s)

    def test_every_gate_has_value_threshold_source(self):
        card = self.world.run()
        for g in card['gates']:
            self.assertIn(g['status'], ('pass', 'fail', 'no-data'))
            self.assertTrue(g['threshold'], g['id'])
            if g['status'] == 'pass':
                self.assertIsNotNone(g['value'], g['id'])
                self.assertTrue(g['sources'], g['id'])

    # ---- newest report per farm
    def test_failing_census_is_a_blocker(self):
        self.world.sub('reports/farm1/%s-census.md' % C, 'clean: 4  failing: 0', 'clean: 3  failing: 1')
        card = self.world.run()
        self.assertEqual(self.gate(card, 'compile.census')['status'], 'fail')
        self.assertEqual(card['verdict'], 'NOT READY')
        self.assertIn('compile.census', card['blockers'])

    def test_newer_failed_run_is_skipped(self):
        self.world.write('reports/farm1/c0ffee000002-census.md', '# census c0ffee000002 FAILED (rc 3)\n',
                         published=self.world.latest() + 60)
        g = self.gate(self.world.run(), 'compile.census')
        self.assertEqual(g['status'], 'pass')
        self.assertEqual(g['sources'][0]['path'], '%s-census.md' % C)

    def test_newer_report_wins(self):
        self.world.edit_json('_commits.json', lambda d: d.__setitem__('c0ffee000002', 0))
        self.world.write('reports/farm1/c0ffee000002-census.md',
                         '# census\n\n- units: 4  clean: 2  failing: 2\n- errors total: 5\n',
                         published=self.world.latest() + 60)
        g = self.gate(self.world.run(), 'compile.census')
        self.assertEqual(g['status'], 'fail')
        self.assertEqual(g['value'], '2/4')
        self.assertEqual(g['sources'][0]['commit'], 'c0ffee000002')

    def test_report_of_another_shard_count_is_rejected(self):
        self.world.sub('reports/fz2/%s-fuzz.md' % C, 'shard 2/2', 'shard 2/32')
        card = self.world.run()
        hard, cov = self.gate(card, 'fuzz.hard'), self.gate(card, 'fuzz.coverage')
        self.assertEqual(hard['status'], 'no-data')
        self.assertIn('shards not covered: 2', hard['note'])
        self.assertEqual(cov['status'], 'fail')

    def set_causes(self, k, causes, path=None):
        """replace the fnvnx-causes block of fz<k>'s report: causes = {cause: (verdict, divergences, worst_of)}"""
        rel = path or 'reports/fz%d/%s-fuzz.md' % (k, C)
        t = self.world.read(rel)
        blk = dict(format='fnvnx-causes/1', causes={c: dict(verdict=v, divergences=n, functions=w, worst_of=w)
                                                    for c, (v, n, w) in causes.items()})
        i = t.index('```json fnvnx-causes')
        self.world.write(rel, t[:i] + '```json fnvnx-causes\n%s\n```\n' % json.dumps(blk))

    def test_fuzz_table_without_causes_block(self):
        rel = 'reports/fz1/%s-fuzz.md' % C
        t = self.world.read(rel)
        self.world.write(rel, t[:t.index('## Causes by verdict class')].replace('| x87-precision |', '| call-flow |'))
        self.world.sub('reports/fz2/%s-fuzz.md' % C, '```json fnvnx-causes', '```text')
        g = self.gate(self.world.run(), 'fuzz.hard')
        self.assertEqual((g['status'], g['value']), ('fail', 1))
        self.assertIn('call-flow 1', g['note'])
        self.assertIn('no fnvnx-causes block', g['note'])

    def test_fuzz_artifacts_and_by_design_do_not_block(self):
        self.set_causes(1, {'new-null': ('artifact', 3, 3), 'x87-precision': ('by-design', 1, 1)})
        card = self.world.run()
        self.assertEqual(self.gate(card, 'fuzz.hard')['status'], 'pass')
        self.assertEqual(card['verdict'], 'READY')
        self.assertNotIn('fuzz.artifacts', card['blockers'])

    def test_fuzz_s13b_summary_when_it_covers_more_shards(self):
        self.world.sub('reports/fz2/%s-fuzz.md' % C, 'shard 2/2', 'shard 2/32')
        summ = dict(format='fnvnx-causes/1', campaigns=['/x/data/c1', '/x/data/c2'],
                    causes={'native-undefined': dict(verdict='real-bug', divergences=7, functions=6, worst_of=6)})
        self.world.write('reports/s13b-causes/causes_all.json', summ, published=self.world.latest() + 60)
        g = self.gate(self.world.run(), 'fuzz.hard')
        self.assertEqual((g['status'], g['value']), ('fail', 6))
        self.assertIn("S13b's campaign summary, 2 of 2 shards", g['note'])
        self.assertEqual(g['sources'][0]['path'], 'causes_all.json')
        self.assertTrue(g['sources'][0]['behind'] is None)               # no commit in the summary
        # the reports win as soon as they cover as many shards
        self.world.sub('reports/fz2/%s-fuzz.md' % C, 'shard 2/32', 'shard 2/2')
        g = self.gate(self.world.run(), 'fuzz.hard')
        self.assertEqual((g['status'], g['value']), ('pass', 0))

    # ---- distances
    def test_old_pass_is_stale(self):
        self.world.edit_json('_commits.json', lambda d: d.__setitem__(C, 30))
        card = self.world.run()
        self.assertEqual(card['verdict'], 'NOT READY')
        self.assertGreater(card['counts']['stale'], 0)
        self.assertTrue(self.gate(card, 'compile.census')['stale'])

    def test_commit_ahead_of_head_is_fresh(self):
        self.world.edit_json('_commits.json', lambda d: d.__setitem__(C, -3))
        card = self.world.run()
        self.assertEqual(card['counts']['stale'], 0)
        self.assertIn('(3 ahead)', card['md'])

    def test_unknown_commit_is_stale(self):
        self.world.edit_json('_commits.json', lambda d: d.pop(C))
        card = self.world.run()
        self.assertTrue(self.gate(card, 'compile.census')['stale'])
        self.assertIn("not on the head's history", card['md'])

    # ---- parsers
    def test_numbers_with_commas_and_a_lone_comma(self):
        d = ready.parse_link_text('trial link: 1,234 objects, 4,459 undefined names, 0 multiple definitions\n'
                                  'x86-inconsistent , ABI manual 2,630\n')
        self.assertEqual((d['objects'], d['undefined'], d['multidef']), (1234, 4459, 0))
        self.assertIsNone(d['x86_inconsistent'])
        self.assertEqual(d['abi_manual'], 2630)

    def test_report_commit_from_path(self):
        self.assertEqual(ready.report_commit('# x\n', 'fa883870e756-full.md'), 'fa883870e756')
        self.assertEqual(ready.report_commit('- commit ' + 'a' * 40 + '\n', 'x.md'), 'a' * 40)

    # ---- ABI
    def test_abi_unexplained_class_fails(self):
        self.world.sub('link/%s/out/abi.tsv' % C, 'pop-mismatch(4)', 'pop-mismatch(4),ref-passes-fewer')
        g = self.gate(self.world.run(), 'abi.x86_inconsistent')
        self.assertEqual((g['status'], g['value']), ('fail', 1))
        self.assertIn('ref-passes-fewer 1', g['note'])

    def test_abi_table_from_nearest_kept_link(self):
        # the newest link report's branch was pruned: the table comes from the kept link branch
        self.world.edit_json('_commits.json', lambda d: d.__setitem__('c0ffee000002', 0))
        self.world.write('reports/link1/c0ffee000002-link.md',
                         self.world.read('reports/link1/%s-link.md' % C).replace(C, 'c0ffee000002'),
                         published=self.world.latest() + 60)
        card = self.world.run()
        self.assertEqual(self.gate(card, 'link.undefined')['sources'][0]['commit'], 'c0ffee000002')
        g = self.gate(card, 'abi.x86_inconsistent')
        self.assertEqual(g['status'], 'pass')
        self.assertEqual(g['sources'][0]['branch'], 'link/' + C)

    def abiclass_world(self):
        """five open pairs, abiclass verdicts for four; S1b sites src/b.cpp 0x500000 (head only) and src/c.cpp
        0x600000 (head and the link's commit)"""
        rows = ''.join('%s\t0x0040%d000\tcallee%d\tref%d\t0\tx86-inconsistent\tpop-mismatch(4),ref-passes-fewer\n'
                       % (u, i, i, i) for i, u in enumerate(('src/a.cpp', 'src/b.cpp#vtables', 'src/c.cpp', 'src/d.cpp',
                                                             'src/e.cpp'), 3))
        self.world.write('link/%s/out/abi.tsv' % C, self.world.read('link/%s/out/abi.tsv' % C) + rows)
        tab = '#verdict\tboot\tsrc\tcaller\tcallee\tref_msvc\tabi_verdict\tclasses\n'
        for v, u, i, caller in (('benign-more', 'src/a.cpp', 3, '00450000'), ('patched', 'src/b.cpp', 4, '00500000'),
                                ('benign-unread', 'src/b.cpp', 4, '00510000'),
                                ('patched', 'src/c.cpp', 5, '00600000'), ('fix-donor', 'src/d.cpp', 6, '00700000')):
            tab += '%s\t2\t%s\t%s\tcallee%d\tref%d\tx86-inconsistent\tpop-mismatch,ref-passes-fewer\n' % (
                v, u, caller, i, i)
        self.world.write('link/%s/out/abiclass.tsv' % C, tab)
        self.world.write('patches/x.s1b', '@@ XINC1 src/b.cpp 0x00500000\n+x\n@@ XINC1 src/c.cpp 0x00600000\n+y\n',
                         port=True)
        self.world.write('_patches/%s/x.s1b' % C, '@@ XINC1 src/c.cpp 0x00600000\n+y\n')

    def test_abiclass_patched_and_benign_explain(self):
        self.abiclass_world()
        g = self.gate(self.world.run(), 'abi.x86_inconsistent')
        self.assertEqual((g['status'], g['value']), ('fail', 3))        # c (patch already in the link), d, e
        self.assertEqual(g['abiclass'], {'benign-more': 1, 'patched': 1, 'patched-in-link': 1, 'fix-donor': 1,
                                         'no-verdict': 1})
        self.assertIn('2 by S1b abiclass verdicts (benign-more 1, patched 1)', g['note'])
        self.assertIn('open by abiclass verdict: fix-donor 1, no-verdict 1, patched-in-link 1', g['note'])
        self.assertEqual(g['sources'][1]['path'], 'out/abiclass.tsv')
        # the head dropped b's hunk: its verdict is stale; the link's patches unknown: unverified
        self.world.write('patches/x.s1b', '@@ XINC1 src/c.cpp 0x00600000\n+y\n', port=True)
        self.assertEqual(self.gate(self.world.run(), 'abi.x86_inconsistent')['abiclass'].get('patched-gone'), 1)
        self.world.write('patches/x.s1b', '@@ XINC1 src/b.cpp 0x00500000\n+x\n', port=True)
        shutil.rmtree(self.world.path('_patches'))
        self.assertEqual(self.gate(self.world.run(), 'abi.x86_inconsistent')['abiclass'].get('patched-unverified'), 1)

    def test_abiclass_all_explained_passes(self):
        self.abiclass_world()
        self.world.sub('link/%s/out/abi.tsv' % C, 'src/c.cpp', 'src/a.cpp')
        t = self.world.read('link/%s/out/abi.tsv' % C)
        self.world.write('link/%s/out/abi.tsv' % C, ''.join(l + '\n' for l in t.splitlines()
                                                           if 'src/d.cpp' not in l and 'src/e.cpp' not in l))
        self.world.sub('link/%s/out/abiclass.tsv' % C, 'patched\t2\tsrc/c.cpp\t00600000',
                       'benign-conv\t2\tsrc/a.cpp\t00600000')
        card = self.world.run()
        g = self.gate(card, 'abi.x86_inconsistent')
        self.assertEqual((g['status'], g['value']), ('pass', 0), g['note'])
        self.assertEqual(card['verdict'], 'READY')

    def test_abiclass_dir_of_the_gates_job(self):
        self.abiclass_world()
        d = os.path.join(self.world.tmp, 'abiclass')
        os.makedirs(d)
        writef(os.path.join(d, C + '.tsv'), '#verdict\tboot\tsrc\tcaller\tcallee\tref_msvc\tabi_verdict\tclasses\n')
        g = self.gate(self.world.run('--abiclass', d), 'abi.x86_inconsistent')
        self.assertEqual((g['value'], g['abiclass']), (5, {'no-verdict': 5}))
        self.assertEqual(g['sources'][1]['branch'], 'gates job')
        self.world.edit_json('thresholds.json', lambda t: t.pop('abi_explained_verdicts'), port=True)
        self.assertIn('abi_explained_verdicts not set', self.gate(self.world.run(), 'abi.x86_inconsistent')['note'])

    # ---- S11 / S12
    def test_equiv_by_design_comes_from_patches(self):
        self.world.sub('docs/PATCHES.md', '| 0x00402000 |', '| 0x00409000 |', port=True)
        g = self.gate(self.world.run(), 'equiv.code')
        self.assertEqual((g['status'], g['value']), ('fail', 1))
        self.assertIn('STATIC1 1', g['note'])

    def test_static_class_closed_only_while_fix_is_present(self):
        rel = 'reports/st1/%s-static.md' % C
        self.world.sub(rel, 'src/b.cpp 20 F2S\n', 'src/b.cpp 20 F2S\nsrc/c.cpp 30 BARRIER\n')
        self.assertEqual(self.gate(self.world.run(), 'static.hazards')['status'], 'pass')
        self.world.write('shim/include/fnvnx_compat.h', 'static inline void _ReadWriteBarrier(void) {}\n', port=True)
        g = self.gate(self.world.run(), 'static.hazards')
        self.assertEqual((g['status'], g['value']), ('fail', 1))
        self.assertIn('BARRIER 1', g['note'])

    def test_static_class_closed_by_the_build_config(self):
        # tools/static/report.py's CLOSED (build_closures() reads compile.sh): closed while the plugin is loaded
        rel = 'reports/st1/%s-static.md' % C
        self.world.sub(rel, 'src/b.cpp 20 F2S\n', 'src/b.cpp 20 F2S\nsrc/d.cpp 40 VOLATILE-DECL\n')
        g = self.gate(self.world.run(), 'static.hazards')
        self.assertEqual(g['status'], 'pass')
        self.assertIn('VOLATILE-DECL', g['closed'])
        self.assertIn('VOLATILE-DECL 1', g['note'])
        self.world.write('tools/link/compile.sh', '#!/bin/sh\n# loads -fplugin=fnvnx_ftoi\n', port=True)
        g = self.gate(self.world.run(), 'static.hazards')
        self.assertEqual((g['status'], g['value']), ('fail', 1))
        self.assertIn('top open: VOLATILE-DECL 1', g['note'])

    def test_static_doc_key_is_not_a_class(self):
        th = json.loads(self.world.read('thresholds.json', port=True))
        self.assertIn('_doc', th['static_closed'])
        self.assertNotIn('_doc', self.gate(self.world.run(), 'static.hazards')['note'])

    # ---- boot
    def test_synthesized_image_fails_the_milestones(self):
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.__setitem__('boot_image_sha256', 'cd' * 32))
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.real_exe')['status'], 'fail')
        self.assertEqual(self.gate(card, 'boot.main_menu')['status'], 'fail')
        self.assertEqual(card['blockers'][0], 'boot.real_exe')

    def test_synthesized_image_named_in_the_report(self):
        # no link/<c12> kept, so no hash: the report's data image line decides
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.pop('boot_image_sha256'))
        self.world.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.update(image=None))
        self.assertEqual(self.gate(self.world.run(), 'boot.real_exe')['status'], 'no-data')
        self.world.sub('reports/full1/%s-full.md' % C, "data image: the owner's FalloutNV.exe",
                       'data image: $FARM_HOME/full/build/boot/synth/synth.exe')
        g = self.gate(self.world.run(), 'boot.real_exe')
        self.assertEqual((g['status'], g['value']), ('fail', 'no (synthesized image)'))

    def test_v1_audits_from_the_newest_report_that_ran_them(self):
        self.world.edit_json('_commits.json', lambda d: d.__setitem__('c0ffee000002', 0))
        t = self.world.read('reports/full1/%s-full.md' % C)
        t = t[:t.index('## V1 audits')] + '- V1-link audits: not run (no link outputs)\n'
        self.world.write('reports/full1/c0ffee000002-full.md', t.replace(C, 'c0ffee000002'),
                         published=self.world.latest() + 60)
        card = self.world.run()
        self.assertEqual(self.gate(card, 'link.traps')['sources'][0]['commit'], 'c0ffee000002')
        g = self.gate(card, 'link.v1')
        self.assertEqual((g['status'], g['sources'][0]['commit']), ('pass', C))

    def test_milestones_from_meta_keys(self):
        self.world.remove('link/%s/boot/milestones.json' % C)
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.update(boot_main_menu=1, boot_first_frame=0))
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.winmain')['status'], 'pass')        # from the report line
        self.assertEqual(self.gate(card, 'boot.main_menu')['status'], 'pass')
        self.assertEqual(self.gate(card, 'boot.first_frame')['status'], 'fail')
        self.assertEqual(self.gate(card, 'boot.first_cell')['status'], 'no-data')
        self.assertIn('boot.first_cell', card['missing'])

    def test_milestones_s5_format(self):
        # tests/s5/boot_milestones.py: null is not measured, false is not reached; the stub boot is not progress
        def edit(d):
            d.update(first_frame=False, first_cell=None, stop='fault at pc=0x1234 / UC_ERR_FETCH_UNMAPPED')
            d['stub'].update(first_frame=True)
        self.world.edit_json('link/%s/boot/milestones.json' % C, edit)
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.renderer_init')['status'], 'pass')
        ff = self.gate(card, 'boot.first_frame')
        self.assertEqual((ff['status'], ff['value']), ('fail', 'no'))
        self.assertIn('stub-fault boot reaches it', ff['note'])
        self.assertEqual(self.gate(card, 'boot.first_cell')['status'], 'no-data')
        for gid in card['by_id']:
            self.assertNotIn(gid, ('boot.schema', 'boot.stub', 'boot.image', 'boot.stop'))
        self.world.edit_json('link/%s/boot/milestones.json' % C, lambda d: d['stub'].update(first_frame=False))
        self.assertIn('raw boot stops: fault at pc=0x1234', self.gate(self.world.run(), 'boot.first_frame')['note'])

    def test_image_label_without_a_hash(self):
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.pop('boot_image_sha256'))
        g = self.gate(self.world.run(), 'boot.real_exe')
        self.assertEqual(g['status'], 'pass')
        self.assertIn('S5 label', g['note'])
        self.world.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.update(image='synthesized'))
        card = self.world.run()
        # the report still says the owner's exe: contradicting labels, none stands (V1 R13)
        g = self.gate(card, 'boot.real_exe')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('contradict', g['note'])
        self.assertEqual(self.gate(card, 'boot.main_menu')['status'], 'fail')
        self.world.sub('reports/full1/%s-full.md' % C, "data image: the owner's FalloutNV.exe",
                       'data image: build/boot/synth.exe')
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.real_exe')['status'], 'fail')
        self.assertEqual(self.gate(card, 'boot.main_menu')['status'], 'fail')
        # an unknown label value contradicts a known one
        self.world.sub('reports/full1/%s-full.md' % C, 'data image: build/boot/synth.exe',
                       "data image: the owner's FalloutNV.exe")
        self.world.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.update(image='player exe'))
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.update(boot_image='patched exe'))
        self.assertEqual(self.gate(self.world.run(), 'boot.real_exe')['status'], 'no-data')

    def test_scorecard_one_number_per_stage(self):
        card = self.world.run()
        rows = {(r['stage'], r['what'].split(' (')[0]): r for r in card['scorecard']}
        self.assertEqual([r['stage'] for r in card['scorecard']],
                         ['compile', 'link', 'link', 'ABI', 'fuzz', 'equivalence', 'boot', 'render parity',
                          'frame budget', 'frame budget'])
        for r in card['scorecard'][:8]:
            self.assertEqual((r['gap'], r['status']), (0, 'done'), r)
            self.assertTrue(r['sources'], r)
        # the frame rows: 60 fps at 1.02 GHz (J-0150); the S15 budget fits (12.0 ms), the GPU model does not
        fb, gpu = card['scorecard'][8], card['scorecard'][9]
        self.assertEqual((fb['value'], fb['target'], fb['unit'], fb['gap'], fb['status']), (12.0, 16.7, 'ms', 0, 'done'))
        self.assertEqual((gpu['value'], gpu['status']), (24.8, 'open'))
        self.assertAlmostEqual(gpu['gap'], 24.8 - 16.7, places=1)
        self.assertEqual(card['verdict'], 'READY')
        # a census with errors, an S11 diff, a missing S16 file
        self.world.sub('reports/farm1/%s-census.md' % C, 'errors total: 0', 'errors total: 3')
        self.world.remove('reports/s16/%s-frames.json' % C)
        card = self.world.run()
        sc = {r['stage']: r for r in card['scorecard']}
        self.assertEqual((sc['compile']['value'], sc['compile']['gap'], sc['compile']['status']), (3, 3, 'open'))
        self.assertEqual((sc['render parity']['gap'], sc['render parity']['status']), (None, 'no-data'))
        md = readf(os.path.join(self.world.tmp, 'card.md'))
        self.assertIn('## Scorecard', md)
        self.assertIn('| compile | census errors | 3 | 0 | 3 | **open** |', md)

    def test_fuzz_coverage_per_shard(self):
        g = self.gate(self.world.run(), 'fuzz.coverage')
        self.assertEqual(g['status'], 'pass')
        self.assertEqual(set(g['per_shard']), {'1', '2'})
        # shard 2 at 8 of 10: 90 % in total would pass, but shard 2 alone is below 90 %
        self.world.sub('reports/fz2/%s-fuzz.md' % C, 'cumulative (verdicts valid for this head): 10 functions',
                       'cumulative (verdicts valid for this head): 8 functions')
        g = self.gate(self.world.run(), 'fuzz.coverage')
        self.assertEqual(g['status'], 'fail')
        self.assertIn('shards below 90 %: 2', g['note'])

    def test_try_farms_never_count(self):
        """frames, frameprof and the frame budget of a try farm (unmerged wip heads) are not evidence (V1 R14)"""
        self.world.edit_json('reports/s16/%s-frames.json' % C, lambda d: d['scenes'][0].update({'pass': False}))
        self.world.write('reports/try1of4/aaaaaaaaaaaa-frames.json', {'commit': 'aaaaaaaaaaaa', 'scenes': [
            {'name': 'main_menu', 'pass': True}]}, published=self.world.latest() + 60)
        self.world.write('reports/try2of4/aaaaaaaaaaaa-frameprof.json', [{'label': 'frame 1', 'instructions': 0}],
                         published=self.world.latest() + 60)
        card = self.world.run()
        g = self.gate(card, 'frames.compare')
        self.assertEqual((g['status'], g['sources'][0]['branch']), ('fail', 'reports/s16'))
        self.assertEqual(self.gate(card, 'perf.frame.boost')['sources'][0]['branch'], 'reports/prof1')

    def test_peak_heap_from_milestones(self):
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.pop('boot_peak_heap_bytes'))
        self.world.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.update(boot_peak_heap_bytes=1900 << 20))
        self.assertEqual(self.gate(self.world.run(), 'perf.memory')['status'], 'fail')

    def test_milestones_from_summary_lines(self):
        self.world.remove('link/%s/boot/milestones.json' % C)
        self.world.write('link/%s/boot/summary.txt' % C, 'fnvnx milestone: main_menu\nfnvnx milestone: first_frame\n'
                         'fnvnx milestone: first_cell\n')
        card = self.world.run()
        for m in ('main_menu', 'first_frame', 'first_cell'):
            self.assertEqual(self.gate(card, 'boot.' + m)['status'], 'pass', m)

    # ---- frames
    def test_frames_scene_fails(self):
        self.world.edit_json('reports/s16/%s-frames.json' % C, lambda d: d['scenes'].append(
            dict(name='first_cell', metric='ssim', value=0.5, threshold=0.98, **{'pass': False})))
        g = self.gate(self.world.run(), 'frames.compare')
        self.assertEqual((g['status'], g['value'], g['note']), ('fail', '1/2', 'first_cell'))

    # ---- performance: three levels, two clocks

    def test_frame_levels_per_clock(self):
        card = self.world.run()
        stock, boost = self.gate(card, 'perf.frame.stock'), self.gate(card, 'perf.frame.boost')
        # 11 M instructions at IPC 0.8: 13.5 ms at 1.02 GHz, 7.7 ms at 1.785 GHz; J-0150: 60 fps at the stock clock
        self.assertEqual(stock['levels'], dict(floor=True, info=True, target=True, level='target'))
        self.assertEqual(boost['levels'], dict(floor=True, info=True, target=True, level='target'))
        self.assertEqual((stock['status'], stock['required']), ('pass', True))
        self.assertEqual((boost['status'], boost['required']), ('pass', False))
        self.assertEqual(card['verdict'], 'READY')
        # 15 M: 18.4 ms at 1.02 GHz misses 60 fps there (counts), 10.5 ms at 1.785 GHz (informative)
        self.world.write('reports/prof1/%s-frameprof.json' % C, [dict(label='frame 1', instructions=15000000)])
        card = self.world.run()
        stock, boost = self.gate(card, 'perf.frame.stock'), self.gate(card, 'perf.frame.boost')
        self.assertEqual((stock['status'], stock['levels']['level']), ('fail', 'below target'))
        self.assertEqual(boost['status'], 'pass')
        self.assertIn('perf.frame.stock', card['blockers'])
        self.assertNotIn('perf.frame.boost', card['blockers'])

    def test_frame_below_floor(self):
        self.world.write('reports/prof1/%s-frameprof.json' % C,
                         [dict(label='frame 1', instructions=20000000), dict(label='frame 2', instructions=60000000)])
        card = self.world.run()
        stock = self.gate(card, 'perf.frame.stock')
        self.assertEqual(stock['levels']['level'], 'fail')             # 73.5 ms worst at 1.02 GHz
        self.assertEqual(stock['status'], 'fail')
        self.assertIn('perf.frame.stock', card['blockers'])
        self.assertEqual(self.gate(card, 'perf.boot')['status'], 'no-data')

    def test_worst_frame_decides_not_the_median(self):
        # J-0150: 60 fps on the worst frame; nine frames at 9.8 ms and one at 19.6 ms (1.02 GHz) miss it
        frames = [dict(label='frame %d' % i, instructions=8000000) for i in range(9)]
        frames.append(dict(label='frame 9', instructions=16000000))
        self.world.write('reports/prof1/%s-frameprof.json' % C, frames)
        g = self.gate(self.world.run(), 'perf.frame.stock')
        self.assertEqual(g['levels'], dict(floor=True, info=True, target=False, level='below target'))
        self.assertEqual(g['status'], 'fail')

    def test_render_share_per_clock(self):
        card = self.world.run()
        stock, boost = self.gate(card, 'perf.render.stock'), self.gate(card, 'perf.render.boost')
        # renderer share of the frame (max_budget_fraction 0.2): stock 6 / 9 / 12 %, boost 3.5 / 5 / 7 %
        self.assertEqual(stock['levels'], dict(floor=True, info=True, target=True, level='target'))
        self.assertEqual(boost['levels'], dict(floor=True, info=True, target=True, level='target'))
        self.assertTrue(stock['value'].startswith('12 % of the 60 fps frame (2.0 ms)'), stock['value'])
        self.assertEqual((stock['required'], boost['required']), (True, False))

    def test_render_uses_budget_fraction_not_targets(self):
        # `targets` says pass (the renderer alone against the whole frame); the share of the frame decides
        self.world.edit_json('_render/report.json', lambda d: d['summary']['targets_budget_fraction']['60'].update(
            {'1.02': 0.273}))
        card = self.world.run()
        g = self.gate(card, 'perf.render.stock')
        self.assertEqual((g['status'], g['levels']['level']), ('fail', 'below target'))
        self.assertIn('perf.render.stock', card['blockers'])

    def test_render_fractions_from_frame_ms(self):
        # no targets_budget_fraction: the gate (hi) values, measured at the lowest clock of assumptions.clock_ghz
        self.world.edit_json('_render/report.json', lambda d: d['summary'].pop('targets_budget_fraction'))
        card = self.world.run()
        stock, boost = self.gate(card, 'perf.render.stock'), self.gate(card, 'perf.render.boost')
        self.assertEqual(stock['levels']['level'], 'target')            # 2.0 ms: 12 % of 16.7
        self.assertEqual(boost['levels']['level'], 'target')            # 1.1 ms at 1.785 GHz
        self.assertIn('frame_ms gate values', stock['note'])
        self.world.edit_json('_render/report.json', lambda d: d['summary']['frame_ms'].update(main=4.0))
        # 4.0 ms: 24 % of 16.7 (no), 18 % of 22.2, 12 % of 33.3
        self.assertEqual(self.gate(self.world.run(), 'perf.render.stock')['levels']['level'], 'below target')
        self.world.edit_json('_render/report.json', lambda d: d['summary']['frame_ms'].update(main=40.0, render=20.0))
        self.assertEqual(self.gate(self.world.run(), 'perf.render.stock')['levels']['level'], 'fail')

    def test_gpu_model_is_informative(self):
        card = self.world.run()
        dock, hh = self.gate(card, 'perf.gpu.docked_720p'), self.gate(card, 'perf.gpu.handheld_720p')
        self.assertEqual((dock['value'], dock['levels']['level']), ('7.4 - 12.4 ms', 'target'))
        # 24.8 ms: above 30 fps, below the informational 45 fps, far from 60
        self.assertEqual((hh['status'], hh['levels']['level'], hh['required']), ('fail', 'below info', False))
        self.assertEqual(self.gate(card, 'perf.gpu.handheld_540p')['levels']['level'], 'target')
        self.assertEqual(card['verdict'], 'READY')
        self.assertIn('model, not measured', hh['note'])
    def test_render_top_level_block_and_missing_file(self):
        s = json.loads(self.world.read('_render/report.json'))['summary']
        self.world.write('_render/report.json', s)
        self.assertEqual(self.gate(self.world.run(), 'perf.render.stock')['status'], 'pass')
        self.world.remove('_render/report.json')
        card = self.world.run()
        g = self.gate(card, 'perf.render.stock')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('no render perf report', g['note'])
        self.assertIn('perf.render.stock', card['missing'])

    def test_frame_budget_per_clock(self):
        card = self.world.run()
        stock, boost = self.gate(card, 'perf.budget.stock'), self.gate(card, 'perf.budget.boost')
        self.assertEqual((stock['status'], stock['level'], stock['required']), ('pass', 'target_60', True))
        self.assertEqual((boost['status'], boost['required']), ('pass', False))
        self.assertIn('plan45 target_60', stock['note'])
        # lo fits 16.7 ms, hi does not: uncertain, whatever S15's verdicts say
        self.world.edit_json('reports/prof1/%s-frame-budget.json' % C, lambda d: d['scenarios'][0]['results'][
            '1020']['worst_frame_ms'].update(lo=12.0, mid=15.0, hi=20.0))
        g = self.gate(self.world.run(), 'perf.budget.stock')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('needs a measured frame', g['note'])

    def test_frame_budget_fail_and_model_only(self):
        rel = 'reports/prof1/%s-frame-budget.json' % C
        wf = lambda **k: lambda d: d['scenarios'][0]['results']['1020']['worst_frame_ms'].update(**k)
        self.world.edit_json(rel, wf(lo=18.0, mid=22.0, hi=25.0))       # even the lo case misses 16.7 ms
        card = self.world.run()
        self.assertEqual(self.gate(card, 'perf.budget.stock')['status'], 'fail')
        self.assertEqual(card['verdict'], 'NOT READY')
        self.world.edit_json(rel, lambda d: (wf(lo=7.0, mid=12.0, hi=16.0)(d), d['inputs'].update(frameprof=False)))
        g = self.gate(self.world.run(), 'perf.budget.stock')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('model only', g['note'])
    def test_frame_budget_from_the_checkout_model(self):
        self.world.remove('reports/prof1/%s-frame-budget.json' % C)
        self.assertEqual(self.gate(self.world.run(), 'perf.budget.boost')['note'], 'no fnvnx-frame-budget/1 file')
        model = dict(schema='fnvnx-frame-budget/1', inputs=dict(frameprof=False), scenarios=[dict(
            id='placed', results={'1785': dict(worst_frame_ms=dict(lo=7, mid=17.2, hi=39.4),
                                               verdicts=dict(pass_45='uncertain'), level='target_60')})])
        self.world.write('docs/PERF_budget.json', model, port=True)
        g = self.gate(self.world.run(), 'perf.budget.boost')
        self.assertEqual((g['status'], g['sources'][0]['path']), ('no-data', 'docs/PERF_budget.json'))
        self.assertEqual(g['value'], 'worst frame 17.2 ms mid (7 - 39.4)')

    def test_gpu_config_alias(self):
        def edit(d):
            sm = d['gpu']['summary_ms']['mix_hi']
            sm['handheld_540p_dynres'] = sm.pop('handheld_540p')
        self.world.edit_json('_render/report.json', edit)
        self.assertEqual(self.gate(self.world.run(), 'perf.gpu.handheld_540p')['value'], '9.0 - 15.1 ms')

    def test_memory_over_budget(self):
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.__setitem__('boot_peak_heap_bytes', 1900 << 20))
        self.assertEqual(self.gate(self.world.run(), 'perf.memory')['status'], 'fail')

    def test_core_placement_problem(self):
        self.world.sub('toolchain/host/placement.h', '{"game",   -1, 0x6,', '{"game",   -1, 0x7,', port=True)
        g = self.gate(self.world.run(), 'perf.cores')
        self.assertEqual(g['status'], 'fail')
        self.assertIn('game may run on core 0', g['note'])

    # ---- packaging
    def test_placeholder_program_id(self):
        self.world.sub('reports/full1/%s-full.md' % C, 'program id 0100aaaabbbb0000', 'program id 0100000000000000')
        g = self.gate(self.world.run(), 'package.program_id')
        self.assertEqual(g['status'], 'fail')
        self.assertIn('PROGRAM_ID', g['note'])

    def test_check_package_rc(self):
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.__setitem__('check_package_rc', 2))
        self.assertEqual(self.gate(self.world.run(), 'package.check')['value'], 'exit 2')
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.pop('check_package_rc'))
        self.assertEqual(self.gate(self.world.run(), 'package.check')['status'], 'no-data')

    # ---- evidence that does not show what the gate claims (V1's R1-R12, tests/v1/test_ready_adversarial.py)
    def test_shard_beyond_the_count_is_no_shard(self):
        self.world.sub('reports/farm3/%s-compile.md' % C, 'shard 2/2', 'shard 3/2')
        g = self.gate(self.world.run(), 'compile.objects')
        self.assertEqual((g['status'], g['note']), ('no-data', 'compile shards with a report: 1 of 2'))
        self.world.sub('reports/eq1/%s-equiv.md' % C, 'shard 1/1', 'shard 0/1')
        self.assertEqual(self.gate(self.world.run(), 'equiv.code')['status'], 'no-data')

    def test_scene_pass_must_be_true(self):
        self.world.edit_json('reports/s16/%s-frames.json' % C, lambda d: d['scenes'][0].update({'pass': 'false'}))
        self.assertEqual(self.gate(self.world.run(), 'frames.compare')['status'], 'fail')
        self.world.edit_json('reports/s16/%s-frames.json' % C, lambda d: d['scenes'].append('main_menu'))
        g = self.gate(self.world.run(), 'frames.compare')                # a malformed entry: no crash, no pass
        self.assertEqual((g['status'], g['value']), ('fail', '0/2'))

    def s16_gate(self, statuses, kind='port', top=None, commit=C):
        sc = [dict(id='scene%d' % i, status=st, frames=[]) for i, st in enumerate(statuses)]
        top = top or ('pass' if all(s == 'pass' for s in statuses) else 'fail')
        self.world.write('reports/s16/%s-frames.json' % C, dict(
            format='fnvnx-gate/1', gate='S16-compare', status=top, candidate=dict(kind=kind, commit=commit),
            summary=dict(scenes=len(sc)), scenes=sc))

    def test_s16_gate_file(self):
        self.s16_gate(['pass', 'pass'])
        g = self.gate(self.world.run(), 'frames.compare')
        self.assertEqual((g['status'], g['value'], g['stale']), ('pass', '2/2', False))
        self.s16_gate(['pass', 'unstable'], top='incomplete')
        g = self.gate(self.world.run(), 'frames.compare')
        self.assertEqual((g['status'], g['value']), ('fail', '1/2'))
        self.assertIn('scene1 unstable', g['note'])
        self.s16_gate(['pass'], top='fail')                              # scenes and the gate's status disagree
        self.assertEqual(self.gate(self.world.run(), 'frames.compare')['status'], 'fail')
        self.s16_gate(['pass'], kind='nine-replay')                      # not the port's own frames
        g = self.gate(self.world.run(), 'frames.compare')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('not the port', g['note'])
        self.s16_gate(['pass'], commit=None)                             # no candidate commit: stale
        self.assertTrue(self.gate(self.world.run(), 'frames.compare')['stale'])

    def test_broken_frameprof_is_no_measurement(self):
        self.world.write('reports/prof1/%s-frameprof.json' % C, [
            dict(label='boot until WinMain', instructions=800000000), dict(label='frame 1', instructions=0),
            dict(label='frame 2', instructions='many'), 'junk'])
        card = self.world.run()
        for gid in ('perf.frame.stock', 'perf.frame.boost', 'perf.boot'):
            g = self.gate(card, gid)
            self.assertEqual(g['status'], 'no-data', gid)
            self.assertIn('broken frameprof run', g['note'])

    def test_render_report_must_be_plausible(self):
        self.world.edit_json('_render/report.json', lambda d: d['summary'].update(frame_ms=dict(main=-1.0, render=-1.0)))
        card = self.world.run()
        g = self.gate(card, 'perf.render.boost')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('frame_ms.main.value -1.0', g['note'])
        self.world.edit_json('_render/report.json', lambda d: d['summary'].update(
            frame_ms=dict(main=dict(lo=7.0, hi=6.0, gate=6.0), render=0)))
        g = self.gate(self.world.run(), 'perf.render.stock')
        self.assertIn('frame_ms.main lo 7.0 > hi 6.0', g['note'])
        self.assertIn('frame_ms.render.value 0', g['note'])
        self.world.edit_json('_render/report.json', lambda d: (d['summary'].pop('frame_ms'),
                                                                d['summary']['targets_budget_fraction']['45'].update(
                                                                    {'1.785': -0.1})))
        self.assertEqual(self.gate(self.world.run(), 'perf.render.boost')['status'], 'no-data')

    def test_gpu_time_must_be_plausible(self):
        self.world.edit_json('_render/report.json', lambda d: d['gpu']['summary_ms']['mix_hi']['docked_720p'].update(
            {'eff_1.0': -3.0}))
        g = self.gate(self.world.run(), 'perf.gpu.docked_720p')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('not a plausible time', g['note'])

    def test_checksum_file_must_name_the_exe(self):
        self.world.write('gamedata/exe/FalloutNV.exe.sha256', 'ab' * 32 + '  README\n')
        g = self.gate(self.world.run(), 'boot.real_exe')
        self.assertEqual(g['status'], 'no-data')                        # the image hash is not checked: no label pass
        self.assertIn('no line for FalloutNV.exe', g['note'])
        self.world.write('gamedata/exe/FalloutNV.exe.sha256', 'cd' * 32 + '  README\n' + 'AB' * 32 + ' *FalloutNV.exe\n')
        self.assertEqual(self.gate(self.world.run(), 'boot.real_exe')['status'], 'pass')

    def test_checksum_file_must_be_the_pinned_exe(self):
        self.world.edit_json('thresholds.json', lambda d: d.update(player_exe_sha256='ef' * 32), port=True)
        g = self.gate(self.world.run(), 'boot.real_exe')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('holds another exe', g['note'])

    def test_failed_boot_stage_counts_nothing_of_the_link(self):
        self.world.sub('reports/full1/%s-full.md' % C, '| boot | ok |', '| boot | fail |')
        card = self.world.run()
        for gid in ('boot.real_exe', 'boot.winmain', 'boot.first_cell'):
            g = self.gate(card, gid)
            self.assertEqual(g['status'], 'fail', gid)
            self.assertIn('boot stage of this full build failed', g['note'])
        self.world.sub('reports/full1/%s-full.md' % C, '| boot | fail |', '| boot | disabled |')
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.real_exe')['status'], 'no-data')
        self.assertIn('did not run the boot (stage disabled)', self.gate(card, 'boot.main_menu')['note'])

    def test_milestones_json_decides_over_summary_and_meta(self):
        self.world.edit_json('link/%s/boot/milestones.json' % C, lambda d: d.update(first_cell=0, first_frame=None))
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.update(boot_first_frame=1))
        self.world.write('link/%s/boot/summary.txt' % C, 'fnvnx milestone: first_cell\nfnvnx milestone: first_frame\n')
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.first_cell')['status'], 'fail')
        self.assertEqual(self.gate(card, 'boot.first_frame')['status'], 'no-data')       # null: not measured

    def test_malformed_link_json_does_not_crash(self):
        self.world.write('link/%s/meta.json' % C, [1, 2])
        self.world.write('link/%s/boot/milestones.json' % C, 'null')
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.real_exe')['status'], 'no-data')

    # ---- fnvnx-fuzz-summary/1
    def fuzz_summary(self, k, **kw):
        rel = 'reports/fz%d/%s-fuzz.md' % (k, C)
        s = dict(format='fnvnx-fuzz-summary/1', commit=C, shard=k, shards=2, status='complete',
                 functions=dict(with_native_object=10, verdicts=10, not_modelled=0))
        for key, v in kw.items():
            if isinstance(v, dict) and isinstance(s.get(key), dict):
                s[key].update(v)
            else:
                s[key] = v
        self.world.write(rel, self.world.read(rel) + '\n```json fnvnx-fuzz-summary\n%s\n```\n' % json.dumps(s))

    def test_fuzz_summary_counts(self):
        self.fuzz_summary(1)
        self.fuzz_summary(2, status='partial', functions=dict(with_native_object=20, verdicts=16))
        g = self.gate(self.world.run(), 'fuzz.coverage')
        self.assertEqual((g['status'], g['value']), ('fail', '86.7 % (26/30)'))
        self.assertIn('fnvnx-fuzz-summary/1 from 2 shards, partial (pending functions): 2', g['note'])

    def test_fuzz_summary_of_another_shard_or_broken_harness(self):
        self.fuzz_summary(2, shard=1)
        card = self.world.run()
        self.assertEqual(self.gate(card, 'fuzz.coverage')['status'], 'fail')
        self.assertIn('missing: 2', self.gate(card, 'fuzz.coverage')['note'])
        self.world.sub('reports/fz2/%s-fuzz.md' % C, '"shard": 1', '"shard": 2')
        self.world.sub('reports/fz2/%s-fuzz.md' % C, '"complete"', '"harness-broken"')
        self.assertIn('missing: 2', self.gate(self.world.run(), 'fuzz.coverage')['note'])
        self.world.sub('reports/fz2/%s-fuzz.md' % C, '"harness-broken"', '"complete"')
        self.world.sub('reports/fz2/%s-fuzz.md' % C, 'shard 2/2', 'shard 2/2 - HARNESS BROKEN')
        self.assertIn('missing: 2', self.gate(self.world.run(), 'fuzz.coverage')['note'])

    def test_fuzz_more_verdicts_than_functions(self):
        self.fuzz_summary(1, functions=dict(with_native_object=5, verdicts=10))
        self.fuzz_summary(2, functions=dict(with_native_object=5, verdicts=10))
        self.assertEqual(self.gate(self.world.run(), 'fuzz.coverage')['status'], 'fail')

    # ---- output helpers
    def test_blockers_one_per_group(self):
        gates = [dict(id='a', group='G', status='fail', stale=False, rank=1, required=True),
                 dict(id='b', group='G', status='fail', stale=False, rank=2, required=True),
                 dict(id='c', group='H', status='fail', stale=False, rank=3, required=True),
                 dict(id='d', group='I', status='fail', stale=False, rank=0, required=False),
                 dict(id='e', group='J', status='no-data', stale=False, rank=0, required=True)]
        self.assertEqual([g['id'] for g in ready.blockers(gates)], ['a', 'c'])
        self.assertEqual([g['id'] for g in ready.missing(gates)], ['e'])

    def test_fmt_src(self):
        s = lambda f, c, b: dict(branch='reports/' + f, path=c + '-x.md', commit=c, behind=b)
        self.assertEqual(ready.fmt_src([]), '-')
        self.assertIn('(2 ahead)', ready.fmt_src([s('fz1', 'a' * 12, -2)]))
        many = [s('fz%d' % i, 'b' * 12, i) for i in range(1, 11)]
        out = ready.fmt_src(many)
        self.assertIn('10 reports (fz1, fz2, fz3, fz4, fz5, ... fz10 (10))', out)
        self.assertIn('up to 10 behind', out)


if __name__ == '__main__':
    unittest.main()
