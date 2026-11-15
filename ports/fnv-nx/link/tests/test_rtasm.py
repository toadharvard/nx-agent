#!/usr/bin/env python3
"""Test of link/rtasm.py (the memory-ordering class check of the runtime's assembly, CLAUDE.md "memory ordering of runtime calls"): one fixture per
class and violating fixtures, assembled by the one build (realobj: the guest compiler pin and the full plugin set) as .S units of a toy guest root,
beside a compiled C++ unit (it carries the plugin record `.fnvnx.ordt`, so it is not assembly and not judged; a `.ident` in an assembly source adds `.comment` and changes nothing).  Classes: D1 (frame, jmp_buf), BT (before threads, the
call-graph proof), KC (kernel call result), AT (atomic with its documented ordering), RO.  Run: python3 -I <this file> (needs the pinned compiler)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import realobj  # noqa: E402
import rtasm  # noqa: E402

SRC = {
    'd1.S': '''
.ident "a .ident in an assembly source writes .comment: not a compiled object"
.text
.global f_d1
.type f_d1,%function
f_d1:
    stp x29, x30, [sp, #-32]!
    mov x29, sp
    str x0, [sp, #16]
    ldr x1, [sp, #16]
    ldp x29, x30, [sp], #32
    ret
.size f_d1, .-f_d1
.global f_esc
.type f_esc,%function
f_esc:                                    // a frame address handed to a callee, then a frame access: not D1
    stp x29, x30, [sp, #-32]!
    mov x29, sp
    add x0, sp, #16
    bl sink
    ldr x1, [sp, #16]
    ldp x29, x30, [sp], #32
    ret
.size f_esc, .-f_esc
''',
    'jb.S': '''
.text
.global setjmp
.type setjmp,%function
setjmp:
    stp x19, x20, [x0]
    mov x2, sp
    str x2, [x0, #16]
    mov w0, #0
    ret
.size setjmp, .-setjmp
.global longjmp
.type longjmp,%function
longjmp:
    ldp x19, x20, [x0]
    ldr x2, [x0, #400]                    // outside sizeof(jmp_buf): a violation
    mov x0, #1
    ret
.size longjmp, .-longjmp
.global not_jb
.type not_jb,%function
not_jb:
    str x1, [x0]                          // x0 is no jmp_buf here: a violation
    ret
.size not_jb, .-not_jb
''',
    'kc.S': '''
.text
.global svc_wrap
.type svc_wrap,%function
svc_wrap:
    str x1, [sp, #-16]!
    svc #0x1
    ldr x2, [sp], #16
    str w1, [x2]
    ret
.size svc_wrap, .-svc_wrap
.global svc_bad
.type svc_bad,%function
svc_bad:
    svc #0x2
    str w9, [x2]                          // x9 is no svc output: a violation
    ret
.size svc_bad, .-svc_bad
''',
    'at.S': '''
.arch armv8-a+lse
.text
.global __aarch64_swp4_acq_rel
.type __aarch64_swp4_acq_rel,%function
__aarch64_swp4_acq_rel:
    swpal w0, w0, [x1]
    ret
.size __aarch64_swp4_acq_rel, .-__aarch64_swp4_acq_rel
.global __aarch64_ldadd4_relax
.type __aarch64_ldadd4_relax,%function
__aarch64_ldadd4_relax:
    ldadd w0, w0, [x1]
    ret
.size __aarch64_ldadd4_relax, .-__aarch64_ldadd4_relax
.global __aarch64_ldadd4_acq
.type __aarch64_ldadd4_acq,%function
__aarch64_ldadd4_acq:
    ldadd w0, w0, [x1]                    // documented acq, instruction relaxed: a violation
    ret
.size __aarch64_ldadd4_acq, .-__aarch64_ldadd4_acq
.global __aarch64_swp4_sync
.type __aarch64_swp4_sync,%function
__aarch64_swp4_sync:                      // lse.S: swp _sync is acquire only (then dmb ish)
    swpa w0, w0, [x1]
    dmb ish
    ret
.size __aarch64_swp4_sync, .-__aarch64_swp4_sync
.global __aarch64_ldadd4_sync
.type __aarch64_ldadd4_sync,%function
__aarch64_ldadd4_sync:                    // every other _sync op is acq_rel: acquire only is a violation
    ldadda w0, w0, [x1]
    dmb ish
    ret
.size __aarch64_ldadd4_sync, .-__aarch64_ldadd4_sync
.global ordered_pair
.type ordered_pair,%function
ordered_pair:
    ldar w0, [x1]
    stlr w0, [x2]
    ret
.size ordered_pair, .-ordered_pair
''',
    'start.S': '''
.text
.global _start
.type _start,%function
_start:
    stp x29, x30, [sp, #-16]!
    bl init_data
    bl main_ext
    bl late_data
    ldp x29, x30, [sp], #16
    ret
.size _start, .-_start
''',
    'init.S': '''
.text
.global init_data
.type init_data,%function
init_data:
    adrp x0, gvar
    str wzr, [x0, :lo12:gvar]
    ret
.size init_data, .-init_data
.global late_data
.type late_data,%function
late_data:                                // called after the call to main_ext: threads may exist
    adrp x0, gvar
    str wzr, [x0, :lo12:gvar]
    ret
.size late_data, .-late_data
.global tab_data
.type tab_data,%function
tab_data:                                 // reached before threads too, but its address is also in a data table: not BT
    adrp x0, gvar
    str wzr, [x0, :lo12:gvar]
    ret
.size tab_data, .-tab_data
.bss
.global gvar
gvar: .skip 4
.section .data.rel.ro,"aw"
.global tab
tab: .word tab_data
''',
    'jbp.cc': 'typedef long jmp_buf[22];\njmp_buf fnvnx_jmpbuf_probe;\n',
    'c.cc': 'int cc_f(int *p) { return *p; }\n',
}
LSE = '''
#if MODEL == 1
# define SUFF  _relax
# define A
# define L
#elif MODEL == 2
# define SUFF  _acq
# define A     a
# define L
#elif MODEL == 3
# define SUFF  _rel
# define A
# define L     l
#elif MODEL == 4
# define SUFF  _acq_rel
# define A     a
# define L     l
#elif MODEL == 5
# define SUFF  _sync
#ifdef L_swp
#  define A    a
#  define L
#else
#  define A    a
#  define L    l
#endif
# define BARRIER dmb ish
#else
# error
#endif
'''
fails = []


def check(what, ok, got=''):
    print('%s %s%s' % ('PASS' if ok else 'FAIL', what, (': ' + str(got)[:600]) if not ok else ''), flush=True)
    if not ok:
        fails.append(what)


def main():
    res = realobj.build(SRC)
    check('fixtures build', res['status'] == 'green', res.get('failure'))
    if res['status'] != 'green':
        return 1
    OBJP = {os.path.basename(o['unit']): o['object'] for o in res['objects']}
    obj = {k: open(p, 'rb').read() for k, p in OBJP.items()}
    # a compiled object without the plugin record (the recipe refuses a plugin-less guest root, so the record is removed from a built object)
    stripped = os.path.join(os.path.dirname(OBJP['c.cc']), 'np.o')
    subprocess.run(['aarch64-linux-gnu-objcopy', '--remove-section=.fnvnx.ordt', OBJP['c.cc'], stripped], check=True)
    obj['np.c'] = open(stripped, 'rb').read()
    order = ['start.S', 'd1.S', 'jb.S', 'kc.S', 'at.S', 'init.S', 'c.cc', 'np.c']
    objs = rtasm.universe([(n, obj[n], n == 'start.S') for n in order])
    rep = rtasm.judge(objs, jb_names=['setjmp', 'longjmp'], jb_bytes=176, lse=rtasm.lse_models(LSE))
    check('assembled = the .S objects only (the compiled unit carries .fnvnx.ordt; the .S with .ident is still assembly)', sorted(rep['assembled']) == sorted(n for n in order if n.endswith('.S') or n == 'np.c'), rep['assembled'])
    by = {}
    for a in rep['accesses']:
        by.setdefault((a.func, a.cls), []).append(a)
    viol = {}
    for v in rep['violations']:
        viol.setdefault(v.func, []).append(v)
    check('D1: frame accesses of f_d1 (no escape)', len(by.get(('f_d1', 'D1'), [])) >= 4 and 'f_d1' not in viol, (by.get(('f_d1', 'D1')), viol.get('f_d1')))
    check('D1 violated: a frame access after a frame address escaped to a callee', any(v.detail.startswith('plain') for v in viol.get('f_esc', [])), viol.get('f_esc'))
    check('D1 jmp_buf: setjmp accesses through x0', len(by.get(('setjmp', 'D1'), [])) == 2 and 'setjmp' not in viol, (by.get(('setjmp', 'D1')), viol.get('setjmp')))
    check('D1 jmp_buf: an access past sizeof(jmp_buf) is a violation', [v.off for v in viol.get('longjmp', [])] == [4], viol.get('longjmp'))
    check('D1 jmp_buf: a routine that is no jmp_buf routine gets no jmp_buf class', 'not_jb' in viol, viol.get('not_jb'))
    check('KC: the store of svc results through the out pointer', len(by.get(('svc_wrap', 'KC'), [])) == 1 and 'svc_wrap' not in viol, (by.get(('svc_wrap', 'KC')), viol.get('svc_wrap')))
    check('KC violated: a store of a register that is no svc output', 'svc_bad' in viol, viol.get('svc_bad'))
    check('AT: acquire-release form and the documented relax/ordered forms', len(by.get(('__aarch64_swp4_acq_rel', 'AT'), [])) == 1 and len(
        by.get(('__aarch64_ldadd4_relax', 'AT'), [])) == 1 and len(by.get(('ordered_pair', 'AT'), [])) == 2, {k: len(v) for k, v in by.items() if k[1] == 'AT'})
    check('AT: swp _sync is acquire only (lse.S #ifdef L_swp), acq_rel for every other op', len(by.get(('__aarch64_swp4_sync', 'AT'), [])) == 1 and '__aarch64_ldadd4_sync' in viol and '__aarch64_swp4_sync' not in viol, (viol.get('__aarch64_swp4_sync'), viol.get('__aarch64_ldadd4_sync')))
    check('lse_models is read from the lse.S text', rtasm.lse_models(LSE)['sync'] == {'swp': (True, False), 'other': (True, True)}, rtasm.lse_models(LSE))
    check('jmp_buf size is the probe symbol size the compiler computed', rtasm.jmpbuf_size(OBJP['jbp.cc'], 'fnvnx_jmpbuf_probe') == 88, '')
    check('AT violated: the instruction ordering differs from the routine name\'s documented one', '__aarch64_ldadd4_acq' in viol, viol.get('__aarch64_ldadd4_acq'))
    check('BT: init_data (called by the entry before the thread-possible call)', len(by.get(('init_data', 'BT'), [])) == 1, by.get(('init_data', 'BT')))
    check('BT violated: late_data (called after the call to main_ext)', 'late_data' in viol, viol.get('late_data'))
    check('BT violated: tab_data (its address is in a data table)', 'tab_data' in viol, viol.get('tab_data'))
    check('an object compiled without the plugin record is judged as assembly (its accesses are classified, none is skipped)', any(a.obj == 'np.c' for a in rep['accesses'] + rep['violations']) and 'np.c' in rep['assembled'], rep['assembled'])
    check('every access is in exactly one class (accesses + violations = all)', len(rep['accesses']) + len(rep['violations']) == sum(rep['counts'].values()) + len(rep['violations']))
    # jmp_buf routines are read from the runtime's own header
    hdr = 'void longjmp (jmp_buf __jmpb, int __retval);\nint setjmp (jmp_buf __jmpb);\nint sigsetjmp (sigjmp_buf, int);\nint f (int);\n'
    check('jmp_buf routines are read from the header', rtasm.jmpbuf_routines(hdr) == ['longjmp', 'setjmp', 'sigsetjmp'], rtasm.jmpbuf_routines(hdr))
    return 1 if fails else 0


if __name__ == '__main__':
    r = main()
    print('FAILED: %s' % fails if fails else 'all passed')
    sys.exit(r)
