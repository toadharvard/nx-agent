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

    def run(self):
        out = os.path.join(self.tmp, 'card.json')
        md = os.path.join(self.tmp, 'card.md')
        rc = ready.main(['--fixtures', self.w, '--port', self.p, '--thresholds', self.path('thresholds.json', True),
                         '--head', C, '--json', out, '--md', md])
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
        self.assertIn('shards without a 2-shard report: 2', hard['note'])
        self.assertEqual(cov['status'], 'fail')

    def test_fuzz_hard_divergence_not_by_design(self):
        self.world.sub('reports/fz1/%s-fuzz.md' % C, '| x87-precision |', '| call-flow |')
        g = self.gate(self.world.run(), 'fuzz.hard')
        self.assertEqual((g['status'], g['value']), ('fail', 1))
        self.assertIn('call-flow 1', g['note'])

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

    def test_milestones_from_meta_keys(self):
        self.world.remove('link/%s/boot/milestones.json' % C)
        self.world.edit_json('link/%s/meta.json' % C, lambda d: d.update(boot_main_menu=1, boot_first_frame=0))
        card = self.world.run()
        self.assertEqual(self.gate(card, 'boot.winmain')['status'], 'pass')        # from the report line
        self.assertEqual(self.gate(card, 'boot.main_menu')['status'], 'pass')
        self.assertEqual(self.gate(card, 'boot.first_frame')['status'], 'fail')
        self.assertEqual(self.gate(card, 'boot.first_cell')['status'], 'no-data')
        self.assertIn('boot.first_cell', card['missing'])

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
        # 21 M instructions at IPC 0.8: 25.7 ms at 1.02 GHz, 14.7 ms at 1.785 GHz
        self.assertEqual(stock['levels'], dict(floor=True, target=False, stretch=False, level='below target'))
        self.assertEqual(boost['levels'], dict(floor=True, target=True, stretch=True, level='stretch'))
        self.assertEqual((stock['status'], stock['required']), ('fail', False))
        self.assertEqual((boost['status'], boost['required']), ('pass', True))
        self.assertIn('fail, informative', card['md'])
        self.assertNotIn('perf.frame.stock', card['blockers'])

    def test_frame_below_floor(self):
        self.world.write('reports/prof1/%s-frameprof.json' % C,
                         [dict(label='frame 1', instructions=20000000), dict(label='frame 2', instructions=60000000)])
        card = self.world.run()
        boost = self.gate(card, 'perf.frame.boost')
        self.assertEqual(boost['levels']['level'], 'fail')             # 42 ms worst at 1.785 GHz
        self.assertEqual(boost['status'], 'fail')
        self.assertIn('perf.frame.boost', card['blockers'])
        self.assertEqual(self.gate(card, 'perf.boot')['status'], 'no-data')

    def test_stretch_on_median_even_if_unstable(self):
        # worst frame 25.2 ms (below the 45 fps target), median 11.2 ms (60 fps reached)
        frames = [dict(label='frame %d' % i, instructions=16000000) for i in range(9)]
        frames.append(dict(label='frame 9', instructions=36000000))
        self.world.write('reports/prof1/%s-frameprof.json' % C, frames)
        lv = self.gate(self.world.run(), 'perf.frame.boost')['levels']
        self.assertEqual(lv, dict(floor=True, target=False, stretch=True, level='below target'))

    def test_render_scaled_to_each_clock(self):
        self.world.edit_json('_render/report.json', lambda d: d['summary']['frame_ms'].update(main=40.0, render=20.0))
        card = self.world.run()
        stock, boost = self.gate(card, 'perf.render.stock'), self.gate(card, 'perf.render.boost')
        self.assertEqual(stock['levels']['level'], 'fail')             # 40 ms at 1.02 GHz
        self.assertEqual(boost['levels']['level'], 'below target')     # 22.9 ms at 1.785 GHz
        self.assertTrue(boost['value'].startswith('22.9 / 11.4 ms'))
        self.assertEqual(card['verdict'], 'NOT READY')

    def test_render_top_level_block_and_missing_file(self):
        s = json.loads(self.world.read('_render/report.json'))['summary']
        self.world.write('_render/report.json', s)
        self.assertEqual(self.gate(self.world.run(), 'perf.render.boost')['status'], 'pass')
        self.world.remove('_render/report.json')
        card = self.world.run()
        g = self.gate(card, 'perf.render.boost')
        self.assertEqual(g['status'], 'no-data')
        self.assertIn('no render perf report', g['note'])
        self.assertIn('perf.render.boost', card['missing'])

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
