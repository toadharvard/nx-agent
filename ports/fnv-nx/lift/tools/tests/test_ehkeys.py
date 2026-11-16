import os, re, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ehkeys, impgen


class T(unittest.TestCase):
    def test_header_spelling(self):
        h = open(os.path.join(HERE, '..', '..', 'rt', 'msvcrt.h')).read()
        sp = {m.group(1): int(m.group(2), 16) for m in re.finditer(r'#define (FNVL_EH_KEY_\w+)\s+0x([0-9a-f]+)u', h)}
        self.assertEqual(sorted(sp.values()), sorted(k for k, *_ in ehkeys.EH_KEYS))

    def test_bridges_emitted(self):
        import tempfile
        d = tempfile.mkdtemp(); b = os.path.join(d, 'bridge.tsv')
        open(b, 'w').write('dll\tname\tordinal\tconv\tret\targs\tsym\tbind\n')
        ent, blocked, hit, absent = impgen.generate(b, d, (), None, None, None)
        got = {e[0]: e for e in ent}
        for k, sym, conv, args in ehkeys.EH_KEYS:
            self.assertEqual(got[k][1], 'I_%08x' % k)
        src = open(os.path.join(d, 'imp.cpp')).read()
        self.assertIn('fnvl_msvcrt_eh_nested_catch(); REGS();', src)
        self.assertIn('c->ra=M32(sp); esp=sp+4u;', src)


if __name__ == '__main__': unittest.main()
