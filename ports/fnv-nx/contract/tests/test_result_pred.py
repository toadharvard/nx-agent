#!/usr/bin/env python3
"""Synthetic check of argflow.result_predicate (`ret != 0xFFFFFFFF on every path to the site`)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argflow, memfx
import synpe
from synpe import build, pe

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


BRIDGE = 'dll\tname\tordinal\tconv\tret\targs\n' + ''.join('kernel32.dll\t%s\t\tstdcall\ti\t%s\n' % (n, 'i' * c) for n, c in
                                                      (('CreateFileA', 7), ('CreateFileW', 7), ('CreateFileMappingA', 6), ('CloseHandle', 1)))
SET = {('kernel32.dll', 'CreateFileA'), ('kernel32.dll', 'CreateFileW')}


def create(a, name='CreateFileA'):
    a.raw(*([0x6a, 0x00] * 7))
    a.call_imp(name)


def mapping(a, push_hfile):
    a.raw(*([0x6a, 0x00] * 5))
    a.raw(*push_hfile)
    a.call_imp('CreateFileMappingA')


MAP_LEN = 10 + 6 + 1        # five pushes, the hFile push (1 byte forms below), the call


def run(body, K=0xffffffff, framed=False, rel='!='):
    def prog(a):
        a.func('f')
        if framed:
            a.raw(0x55, 0x8b, 0xec, 0x83, 0xec, 0x08)
        body(a)
        a.raw(0xc9, 0xc3) if framed else a.raw(0xc3)
        a.end('f')
    secs, a = build(prog)
    img = synpe.argflow.Image(pe(secs), 'syn.exe', {s: [(s, e)] for s, e in a.funcs.values()})
    info = memfx.ImportInfo(BRIDGE)
    img.import_stack = info.stack
    s, e = a.funcs['f']
    fn = img.func(s)
    site = [v for v, i in sorted(fn.nodes.items()) if i.mnemonic == 'call'][-1]
    r = argflow.result_predicate(img, site, 0, SET, rel, K)
    return r.closed, sorted(r.breaks)


def dominating(a):
    create(a); a.raw(0x83, 0xf8, 0xff, 0x74, MAP_LEN); mapping(a, [0x50])


ok, b = run(dominating)
check('dominating cmp eax,-1 / je', ok, True)


def one_path(a):             # cmp esi,0 ; je skip ; cmp eax,-1 ; je out ; skip: mapping
    create(a); a.raw(0x83, 0xfe, 0x00, 0x74, 0x05, 0x83, 0xf8, 0xff, 0x74, MAP_LEN); mapping(a, [0x50])


ok, b = run(one_path)
check('check on one path only is open', (ok, bool(b)), (False, True))


def clobbered(a):
    create(a); a.raw(0x83, 0xf8, 0xff, 0x83, 0xc3, 0x01, 0x74, MAP_LEN); mapping(a, [0x50])      # add ebx,1 between


ok, b = run(clobbered)
check('flags clobbered between cmp and jcc is open', (ok, any('not decided' in w for _, w in b)), (False, True))


def copy_saved(a):           # mov esi,eax ; cmp esi,-1 ; je out ; push esi
    create(a); a.raw(0x89, 0xc6, 0x83, 0xfe, 0xff, 0x74, MAP_LEN); mapping(a, [0x56])


ok, b = run(copy_saved)
check('result copied to esi, checked there, passed', ok, True)


def other_copy(a):           # A: esi = result; W: eax = other result checked; passes esi
    create(a, 'CreateFileA'); a.raw(0x89, 0xc6); create(a, 'CreateFileW'); a.raw(0x83, 0xf8, 0xff, 0x74, MAP_LEN); mapping(a, [0x56])


ok, b = run(other_copy)
check('checked value is another call result: open', (ok, bool(b)), (False, True))


def jb_idiom(a):             # cmp eax,-1 ; jb +1 ; ret ; mapping      (jb taken = != -1)
    create(a); a.raw(0x83, 0xf8, 0xff, 0x72, 0x01, 0xc3); mapping(a, [0x50])


ok, b = run(jb_idiom)
check('cmp eax,-1 / jb: exactly != -1', ok, True)


def inc_idiom(a):            # mov esi,eax ; inc eax ; jz out ; push esi
    create(a); a.raw(0x89, 0xc6, 0x40, 0x74, MAP_LEN); mapping(a, [0x56])


ok, b = run(inc_idiom)
check('inc / jz idiom with the copy kept in esi', ok, True)


def signed(a):               # cmp eax,-1 ; jl out : not the same condition
    create(a); a.raw(0x83, 0xf8, 0xff, 0x7c, MAP_LEN); mapping(a, [0x50])


ok, b = run(signed)
check('signed range check is not proven', (ok, any('not exactly equivalent' in w for _, w in b)), (False, True))


def slot_copy(a):            # mov [ebp-4],eax ; cmp dword [ebp-4],-1 ; je out ; push dword [ebp-4]
    create(a); a.raw(0x89, 0x45, 0xfc, 0x83, 0x7d, 0xfc, 0xff, 0x74, MAP_LEN + 2); mapping(a, [0xff, 0x75, 0xfc])


ok, b = run(slot_copy, framed=True)
check('copy in a private frame slot', ok, True)


def wrong_k(a):
    create(a); a.raw(0x83, 0xf8, 0x00, 0x74, MAP_LEN); mapping(a, [0x50])


ok, b = run(wrong_k)
check('compare with another constant is open', ok, False)
ok, b = run(dominating, K=0)
check('same code, constant 0 is open', ok, False)


def test_form(a):            # test eax,eax ; je out  (K = 0)
    create(a); a.raw(0x85, 0xc0, 0x74, MAP_LEN); mapping(a, [0x50])


ok, b = run(test_form, K=0)
check('test eax,eax / je proves != 0', ok, True)
ok, b = run(test_form, K=0xffffffff)
check('test eax,eax does not prove != -1', ok, False)

import gate_reach
check('grammar', gate_reach.parse_values('result_of', 'callee=kernel32!CreateFileA|kernel32!CreateFileW; ret != 0xFFFFFFFF on every path to the site'),
      {'callee': [['kernel32.dll', 'CreateFileA'], ['kernel32.dll', 'CreateFileW']], 'where': [], 'ret': [['!=', 0xffffffff]]})
try:
    gate_reach.parse_values('result_of', 'callee=kernel32!CreateFileA; ret == 0 on every path to the site')
    fails.append('grammar: ret == accepted')
except gate_reach.FormatError:
    pass

if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok test_result_pred')
