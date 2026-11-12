"""core.tab_tails takes jump-table slots only from the jtab_tables facts: positive (slots outside the range), negative (no record, red site, no facts -> Unsupported)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fnvlift import core
from fnvlift.core import Unsupported


class Op:
    def __init__(self, scale, index): self.type = core.X.X86_OP_MEM; self.mem = type('M', (), dict(scale=scale, index=index))


class Ins:
    mnemonic = 'jmp'
    size = 7
    def __init__(self, scale=4, index=1): self.operands = [Op(scale, index)]


class Img:
    rset = {0x1003, 0x1103, 0x1203}


FACTS = {'jtab_tables': [{'site': '0x1000', 'base': '0x2000', 'lo': 0, 'hi': 3, 'words': ['0x2000', '0x2004', '0x2008'], 'targets': ['0x1010', '0x5000', '0x5004']}],
         'jtab_red': [{'site': '0x1100', 'why': 'unbounded index'}]}


def run(site, facts):
    core.set_jtab(facts)
    return core.tab_tails(Img(), {site: Ins()}, 0x1000, 0x1100)


def raises(site, facts):
    try:
        run(site, facts)
    except Unsupported as e:
        return str(e)


assert run(0x1000, FACTS) == [0x5000, 0x5004]                     # slots inside [lo, hi) are not tails
assert 'no jtab record' in raises(0x1200, FACTS)
assert 'unbounded index' in raises(0x1100, FACTS)
assert 'no jtab facts' in raises(0x1000, None)
core.set_jtab({'jtab_tables': []})
assert core.tab_tails(Img(), {0x1000: type('N', (Ins,), dict(mnemonic='nop'))()}, 0x1000, 0x1100) == []     # no table site: nothing needed
print('jtab_facts_test ok')
