#!/usr/bin/env python3
"""tests/s13/test_thunkfwd.py - thunk-forward (EQ memory group, farm baseline 588863), on hand-written bytes.

An exe function that is one `jmp rel32` (0x00aa0ab0, 0x00a52d10 ...) forwards its own incoming arguments; its
declaration `void F(void)` names none, so a garbage word the native forwards is not defined by the case.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13thunkfwd'))

import causes    # noqa: E402
import classify  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def main():
    jmp = bytes.fromhex('e9' '4b05' '0000') + b'\xcc' * 11            # jmp 0x00401550 from 0x00401000
    div = dict(kind='call-args', arg='p0', cls='P', x86='00000000', arm='329f574b', arm_garbage=True, index=0,
               target='00401550', callee='?g@@YAXPAX@Z')
    fn = dict(code=jmp, address=0x00401000)
    check(classify._jmp_thunk_target(fn) == 0x00401550, 'jmp rel32 target')
    h = classify.hint(None, None, None, div, fn, 0)
    check(h.get('cause') == 'thunk-forward', 'garbage forwarded by a jmp thunk: thunk-forward (%s)' % h.get('cause'))
    check(causes.policy('thunk-forward')[0] == 'artifact', 'thunk-forward is an artifact')
    check(not classify._thunk_forward(dict(div, target='00401560'), fn), 'another callee than the jump target: not it')
    check(not classify._thunk_forward({k: v for k, v in div.items() if k != 'arm_garbage'}, fn),
          'no native garbage word: not it')
    body = bytes.fromhex('55' '8bec' '5d' 'c3')
    check(not classify._thunk_forward(div, dict(code=body, address=0x00401000)), 'a real function body is not a thunk')
    print('FAILED: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
