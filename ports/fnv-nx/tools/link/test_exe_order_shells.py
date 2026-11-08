#!/usr/bin/env python3
"""add_unit_shell_names: units.json class names for the exe vftables of out/exe_order_classes.tsv (S1's RAWVT1
matches the class owning the member). Hand-made fixtures, no exe.

    python3 tools/link/test_exe_order_shells.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fnvlink  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def main():
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, 'exe_order_classes.tsv'), 'w').write(
            '#class\tvftable\treason\tnative vtable symbol\n'
            'RegSettingCollection\t01082ed8\td22-used\t_ZTV20RegSettingCollection\n')
        uj = os.path.join(d, 'units.json')
        json.dump({'units': [
            {'source': 'a.cpp', 'symbols': {'??_7FUN_005e0830@@6B@': '0x01082ed8',
                                            '??_7Other@@6B@': '0x01099999'},
             'functions': [{'symbol': '??0FUN_005e0830@@QAE@XZ', 'address': '0x005e0830'}]}]}, open(uj, 'w'))
        n = fnvlink.add_unit_shell_names(d, uj, None)
        rows = [l.split('\t') for l in open(os.path.join(d, 'exe_order_classes.tsv')).read().splitlines()[1:]]
        names = {(r[0], r[1], r[2]) for r in rows}
        check(n == 1 and ('FUN_005e0830', '01082ed8', 'd22-used') in names,
              'a ??_7 shell name at a listed vftable is added with its reason: %s' % sorted(names))
        check(not any(r[0] == 'Other' for r in rows), 'a vftable not in the table adds nothing')
        check(('RegSettingCollection', '01082ed8', 'd22-used') in names, 'existing rows stay')
        check(fnvlink.add_unit_shell_names(d, uj, None) == 0, 'idempotent')
        check(fnvlink.add_unit_shell_names(d, None, None) == 0, 'no units.json: nothing')
    finally:
        shutil.rmtree(d)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
