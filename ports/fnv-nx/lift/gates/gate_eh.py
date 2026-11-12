#!/usr/bin/env python3
"""Gate "eh" (fnvnx-gate/1), class D of the verdict: every entry of the C++ EH and SEH tables of every image of the pack is covered by a lifted
landing / handler of the published pack.
    python3 -I gate_eh.py PACK EHFACTS        env GATE_OUT (result dir); writes GATE_OUT/eh.json
PACK     the published pack tree (tools/liftgen.py --finish): PACK/entries.tsv (`addr8 <TAB> symbol <TAB> kind`) and PACK/gate_liftgen.json (`images`).
EHFACTS  the facts directory: EHFACTS/ehtables.json (the exe) and EHFACTS/<module>/ehtables.json, one per image, as tools/ehtables.py writes them
         (tools/universe.py for the exe, tools/modlift.py for a module through pemod: the same parser, rebased addresses).

Universe: every code-address field of every table of every image, counted once per (image, table address, field, target):
  FuncInfo   unwind[i].action          the unwind funclet the runtime calls (rt/msvcrt_eh.c cxx_local_unwind -> funclet)
             try[i].handler[j].addr    the catch funclet (call_catch_block)
             stub[k]                   the `mov eax, FuncInfo; jmp handler` stub must lie in the body of a registered handler (see reg); not itself a target
  reg        frame_regs                the handler H that a `push H` registers on fs:[0] with the stub within 0x30 bytes after H (the stub itself, or a /GS
                                       cookie check before it): rt/seh.c call_handler dispatches H, so H is an entry (ehtables.py `regs`)
  frame_handler                        the target of the stubs' jmp (the CRT's __CxxFrameHandler3, direct transfer)
  catch continuation  cont             the address a catch funclet returns (`mov eax, imm; ret`): the runtime sets c->ra to it, the non-local loop serves it
  SEH scope  rec[i].filter             call_filter (rt/msvcrt_eh.c except3)
             rec[i].handler            the __except block / __finally funclet (c->ra = it)
             handler                   the frame handler pushed on fs:[0] with the table (__except_handler3/4 or a local one): dispatched by call_handler
  ThrowInfo  unwind                    the destructor funclet (pmfnUnwind)
             copy[i]                   the copy constructor of CatchableType i (call_copy_ctor)
Certified: the target is an entry of PACK/entries.tsv of kind `code` (a lifted body L_<addr>); an `import` or `runtime` row is no lifted landing.
A null target is outside the universe only where the MSVC table format defines null as "none": UnwindMapEntry.action == 0 (no action for the state),
ScopeTableEntry.FilterFunc == 0 (a termination handler, __finally: the record's handler is then the finally funclet), ThrowInfo.pmfnUnwind == 0
(no destructor), CatchableType.copyFunction == 0 (trivial copy). No other field of the universe may be 0: a null handler, catch address, stub, scope
handler or continuation is a malformed table and red. (HandlerType.pType == 0 is catch(...), not an address field; ipmap and ESTypeList are data.)

Images. One facts file per image of the pack, nothing certified by absence: red when EHFACTS/ehtables.json is missing, when the number of facts files
differs from gate_liftgen.json `images`, when a facts file lacks the keys below, and when a `code` entry of the pack lies in the executable span
(`text`, [lo, hi)) of no facts image or of two (an image of the pack without facts has its entries in no span). Red as well: a FuncInfo that
failed to parse (`failed_funcinfo`), an SEH scope table without records. An image whose tables are all empty is green: its universe is empty.

Enumeration completeness (tools/ehtables.py), the rule and its assumptions, stated rather than papered over:
  FuncInfo  every decoded `mov eax, imm32; jmp rel32` whose imm32 is a relocated data address holding a FuncInfo magic (0x19930520/1/2) is a frame-handler
            stub; its FuncInfo is parsed with every bound checked. Complete under: every FuncInfo of an x86-32 MSVC image (VC8..VC10, no FH4) is reached
            from such a stub, and the stub is decoded code. Not closed by rule: a FuncInfo reached any other way. The data-side magic scan of ehtables.py
            is a cross-check printed by the generator, not a rule of the gate.
  SEH       `push H; mov eax,fs:[0]` / `push fs:[0]` sites, the scope table being the `push S` right before (inline), or the `push S [; push imm8]` before
            a call of the helper (__SEH_prolog family) that holds the frame push. Not closed: a scope table installed by another idiom (stored
            into the frame by mov), and the end of a v3 table, which the format does not record (parse stops at the first record that fails the
            sanity checks or at the next known table).
  ThrowInfo found as the `push imm32` within 16 bytes before a call of _CxxThrowException (direct, or `call [iat]`). NOT closed by rule: a ThrowInfo passed
            through a wrapper, stored in a table or pushed farther away is not enumerated.
  cont      every `mov eax, imm32(reloc); ret` in the body of a catch funclet. Not closed: a funclet that returns the continuation by another form.
Exit 0 iff green."""
import json
import os
import sys


def hx(v): return int(v, 16) if isinstance(v, str) else int(v)


class Bad(Exception): pass


def need(J, *keys):
    for k in keys:
        if k not in J: raise Bad('no key %s' % k)
    return J


def items(J):
    """-> set of (table address, field, target) of one image's facts; null targets only where the MSVC format says none"""
    need(J, 'funcinfo', 'scopes', 'throwinfo', 'frame_handlers', 'frame_regs', 'conts', 'text', 'failed_funcinfo')
    out = set(); regd = {hx(v) for v in J['frame_regs'].values()}
    def put(t, f, v, none_ok):
        v = hx(v)
        if v == 0 and none_ok: return              # the format's "none"
        if v == 0: raise Bad('null %s of table %s' % (f, t))
        out.add((hx(t), f, v))
    for fi in J['funcinfo']:
        t = fi['addr']
        for i, (to, act) in enumerate(fi['unwind']): put(t, 'unwind[%d].action' % i, act, True)      # UnwindMapEntry.action null: no action
        for i, tr in enumerate(fi['tries']):
            for j, hd in enumerate(tr['handlers']): put(t, 'try[%d].handler[%d].addr' % (i, j), hd['addr'], False)
        for k, st in enumerate(fi['stubs']):
            if hx(st) not in regd: out.add((hx(t), 'stub[%d].unregistered' % k, hx(st)))     # a FuncInfo whose stub no `push H` registers: its handler entry is unknown, never certified
    for hh in J['frame_regs']: put(0, 'reg', hh, False)
    for t in J['frame_handlers']: put(0, 'frame_handler', t, False)
    for k, v in J['conts'].items(): put(v, 'cont', k, False)
    for sc in J['scopes']:
        t = sc['addr']
        if not sc['records']: raise Bad('scope table %s has no records' % t)
        put(t, 'handler', sc['handler'], False)
        for i, (enc, flt, hnd) in enumerate(sc['records']):
            put(t, 'rec[%d].filter' % i, flt, True)              # ScopeTableEntry.FilterFunc null: __finally
            put(t, 'rec[%d].handler' % i, hnd, False)
    for ti in J['throwinfo']:
        t = ti['addr']; put(t, 'unwind', ti['unwind'], True)      # ThrowInfo.pmfnUnwind null: no destructor
        for i, ty in enumerate(ti['types']): put(t, 'copy[%d]' % i, ty['copy'], True)     # CatchableType.copyFunction null: trivial copy
    return out


def load_pack(pack):
    ent = {}
    with open(os.path.join(pack, 'entries.tsv')) as fh:
        for l in fh.read().splitlines():
            if l and not l.startswith('#'):
                a, s, k = l.split('\t'); ent[int(a, 16)] = k
    with open(os.path.join(pack, 'gate_liftgen.json')) as fh: n = int(json.load(fh)['images'])
    return ent, n


def facts(root):
    """-> {image name: path of its ehtables.json}: the exe's (EHFACTS/ehtables.json) and one per subdirectory that has one"""
    res = {}
    if os.path.isfile(os.path.join(root, 'ehtables.json')): res['exe'] = os.path.join(root, 'ehtables.json')
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, 'ehtables.json')
        if os.path.isfile(p): res[d] = p
    return res


def check(pack, root):
    """-> (universe, uncertified list, why)"""
    try:
        ent, nimg = load_pack(pack)
    except (OSError, ValueError, KeyError) as e:
        return 0, [], 'pack unreadable: %s' % e
    if not os.path.isdir(root): return 0, [], 'no EHFACTS directory'
    fs = facts(root); unc = []; uni = 0; spans = []; code = sorted(a for a, k in ent.items() if k == 'code')
    if 'exe' not in fs: return 0, [], 'no ehtables.json for the exe'
    if not code: return 0, [], 'the pack has no code entry'
    if len(fs) != nimg: return 0, [], '%d facts files for %d images of the pack' % (len(fs), nimg)
    for img, p in sorted(fs.items()):
        try:
            with open(p) as fh: J = json.load(fh)
            it = items(J); lo, hi = (hx(x) for x in J['text'])
            if J['failed_funcinfo']: raise Bad('FuncInfo that did not parse: %s' % ' '.join(f['addr'] for f in J['failed_funcinfo'][:5]))
        except (OSError, ValueError, KeyError, TypeError, Bad) as e:
            return 0, [], '%s: ehtables.json unusable: %s' % (img, e)
        spans.append((lo, hi, img)); uni += len(it)
        for t, f, v in it:
            if f.endswith('.unregistered') or ent.get(v) != 'code': unc.append('%s:%08x:%s:%08x' % (img, t, f, v))
    orphan = [a for a in code if sum(1 for lo, hi, _ in spans if lo <= a < hi) != 1]
    if orphan: return uni, sorted(unc), '%d code entries of the pack lie in no image span or in several (first %08x): an image without facts' % (len(orphan), orphan[0])
    return uni, sorted(unc), ''


def main(argv):
    pack, root = argv
    uni, unc, why = check(pack, root)
    if why: status, detail = 'red', why
    elif unc: status, detail = 'red', '%d of %d table targets are no lifted entry' % (len(unc), uni)
    else: status, detail = 'green', '%d of %d table targets are lifted entries' % (uni, uni)
    res = {'format': 'fnvnx-gate/1', 'name': 'eh', 'status': status, 'universe': uni, 'certified': uni - len(unc), 'uncertified': unc[:50], 'detail': detail}
    with open(os.path.join(os.environ['GATE_OUT'], 'eh.json'), 'w') as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == 'green' else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
