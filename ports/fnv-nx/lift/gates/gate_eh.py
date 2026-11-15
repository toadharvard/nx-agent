#!/usr/bin/env python3
"""Gate "eh" (fnvnx-gate/1), class D of the verdict: every entry of the C++ EH and SEH tables of every image of the pack is covered by a lifted
landing / handler of the published pack, and every EH fact the rules could not decide is red.
    python3 -I gate_eh.py PACK EHFACTS        env GATE_OUT (result dir); writes GATE_OUT/eh.json
PACK     the published pack tree (tools/liftgen.py --finish): PACK/entries.tsv (`addr8 <TAB> symbol <TAB> kind`) and PACK/gate_liftgen.json (`images`).
EHFACTS  the facts directory: EHFACTS/ehtables.json (the exe) and EHFACTS/<module>/ehtables.json, one per image, as tools/ehtables.py writes them
         (tools/universe.py for the exe, tools/modlift.py for a module through pemod: the same parser and flow engine tools/ehflow.py, rebased addresses).

Universe: every code-address field of every table of every image, counted once per (image, table address, field, target):
  FuncInfo   unwind[i].action          the unwind funclet the runtime calls (rt/msvcrt_eh.c cxx_local_unwind -> funclet)
             try[i].handler[j].addr    the catch funclet (call_catch_block)
             stub[k].target            the target of the `mov eax, FuncInfo; jmp handler` stub (the CRT's __CxxFrameHandler3, direct transfer)
  registration   handlers              every handler H a registration record of fs:[0] carries (tools/ehtables.py scan_seh: a store of an esp-derived address to fs:[0],
                                       the record = {next, handler, field, trylevel}); rt/seh.c call_handler dispatches H
  catch continuation  cont             the address a catch funclet returns (`mov eax, imm; ret`): the runtime sets c->ra to it, the non-local loop serves it
  SEH scope  rec[i].filter             call_filter (rt/msvcrt_eh.c except3)
             rec[i].handler            the __except block / __finally funclet (c->ra = it)
             handler                   the frame handler that registers the table (__except_handler3/4 or a local one): dispatched by call_handler
  ThrowInfo  unwind                    the destructor funclet (pmfnUnwind)
             copy[i]                   the copy constructor of CatchableType i (call_copy_ctor)
Certified: the target is an entry of PACK/entries.tsv of kind `code` (a lifted body L_<addr>); an `import` or `runtime` row is no lifted landing.
A null target is outside the universe only where the MSVC table format defines null as "none": UnwindMapEntry.action == 0 (no action for the state),
ScopeTableEntry.FilterFunc == 0 (a termination handler, __finally: the record's handler is then the finally funclet), ThrowInfo.pmfnUnwind == 0
(no destructor), CatchableType.copyFunction == 0 (trivial copy). No other field of the universe may be 0: a null handler, catch address, stub target, scope
handler or continuation is a malformed table and red. (HandlerType.pType == 0 is catch(...), not an address field; ipmap and ESTypeList are data.)

Images. One facts file per image of the pack, nothing certified by absence: red when EHFACTS/ehtables.json is missing, when the number of facts files
differs from gate_liftgen.json `images`, when a facts file lacks the keys below, and when a `code` entry of the pack lies in the executable span
(`text`, [lo, hi)) of no facts image or of two (an image of the pack without facts has its entries in no span). An image whose tables are all empty is green.

Red facts (an item no rule resolves is never skipped), per image, each counted in `violations`:
  throw_site          a call of the thrower whose ThrowInfo is not resolved: kind `red` (rule R1: the 2nd argument is a constant, a set of constants, or an incoming argument of the caller
                      resolved over EVERY direct call site, recursively; a rethrow is ThrowInfo 0 with object 0; a function whose address is taken, or that is entered other than by a direct
                      call, has no resolved argument)
  failed_throwinfo    a resolved ThrowInfo address that does not parse as a ThrowInfo
  thrower_escapes     a use of the thrower (or an import slot of it) other than a call: the throw sites are then not all enumerated
  seh_red             a registration of fs:[0] the rules cannot read (R3): a store of an unknown value to fs:[0], a record without a constant handler, a record field of unresolvable form
  helper              a registration helper (a function that registers a record whose field is its incoming argument k) whose argument is not constant at every direct call site (R3 via R1)
  nonconst_stores     a store to the trylevel field of a frame whose value is not a constant (R4); the field is ebp + frame_disp, derived from the registration (R3)
  scope_len           len(records) != 1 + trylevel_max (R4: the table length is 1 + the largest constant trylevel stored; an empty table is valid iff no level >= 0 is stored)
  scope_err           a scope table that does not parse for its length, or whose frame register is not derivable
  failed_funcinfo     a FuncInfo that did not parse
Enumeration completeness (tools/ehtables.py), the rule and its assumptions, stated rather than papered over:
  FuncInfo  every decoded `mov eax, imm32; jmp rel32` whose imm32 is a relocated data address holding a FuncInfo magic (0x19930520/1/2) is a frame-handler
            stub; its FuncInfo is parsed with every bound checked. Complete under ASM-222: every FuncInfo of an x86-32 MSVC image (VC8..VC10, no FH4) is reached
            from such a stub, and the stub is decoded code. Not closed by rule: a FuncInfo reached any other way.
  SEH       every decoded store to fs:[0] is classified: of an esp-derived address (a registration: the record is {next, handler, field, trylevel}), of a loaded value (the unregistration
            restoring the chain: a registration needs a fresh stack address, which a load never yields), or unknown (red). A table is the constant S the field carries (plain or xor-ed
            with a value independent of every argument, the cookie encoding) or the constant a helper's argument k carries at every direct call site.
  ThrowInfo see R1 above; not closed by rule (ASM-223): a ThrowInfo reached through a stored pointer rather than a call argument.
  cont      every `mov eax, imm32(reloc); ret` in the body of a catch funclet. Not closed (ASM-224): a funclet that returns the continuation by another form.
Callee-saved registers across calls are not assumed (tools/ehflow.py): ebx/esi/edi/ebp survive a call only where the callee is proven (every ret restores them) or is a platform import (Win32 as
documented, ASM-003); the proofs are the facts `preserves`, counted globally by tools/ehtables.py.
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


KEYS = ('funcinfo', 'scopes', 'throwinfo', 'conts', 'text', 'failed_funcinfo', 'throw_sites', 'failed_throwinfo', 'thrower_escapes', 'registrations', 'helpers', 'seh_red')


def items(J):
    """-> set of (table address, field, target) of one image's facts; null targets only where the MSVC format says none"""
    need(J, *KEYS)
    out = set()
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
        for k, st in enumerate(fi['stubs']): put(t, 'stub[%d].target' % k, st['target'], False)
    for rg in J['registrations']:
        for hh in rg['handlers']: put(rg['site'], 'frame_handler', hh, False)
    for k, v in J['conts'].items(): put(v, 'cont', k, False)
    for sc in J['scopes']:
        t = sc['addr']
        for hh in sc.get('handlers') or [sc['handler']]: put(t, 'handler', hh, False)
        for i, (enc, flt, hnd) in enumerate(sc['records']):
            put(t, 'rec[%d].filter' % i, flt, True)              # ScopeTableEntry.FilterFunc null: __finally
            put(t, 'rec[%d].handler' % i, hnd, False)
    for ti in J['throwinfo']:
        t = ti['addr']; put(t, 'unwind', ti['unwind'], True)      # ThrowInfo.pmfnUnwind null: no destructor
        for i, ty in enumerate(ti['types']): put(t, 'copy[%d]' % i, ty['copy'], True)     # CatchableType.copyFunction null: trivial copy
    return out


def violations(J):
    """-> {class: [items]} of the red facts of one image"""
    need(J, *KEYS)
    v = {'throw_site': ['%s:%s' % (t['site'], t['red']) for t in J['throw_sites'] if t['kind'] not in ('const', 'rethrow')],
         'failed_throwinfo': ['%s:%s' % (f['site'], f['ti']) for f in J['failed_throwinfo']],
         'thrower_escapes': list(J['thrower_escapes']),
         'seh_red': ['%s:%s' % (f['site'], f['why']) for f in J['seh_red']],
         'helper': ['%s:%s' % (f['addr'], f['err']) for f in J['helpers'] if f['err']],
         'nonconst_stores': ['%s:%s' % (sc['addr'], x) for sc in J['scopes'] for x in sc['nonconst_stores']],
         'scope_len': ['%s:%d!=1+%d' % (sc['addr'], len(sc['records']), sc['trylevel_max']) for sc in J['scopes'] if len(sc['records']) != 1 + sc['trylevel_max']],
         'scope_err': ['%s:%s' % (sc['addr'], sc['err']) for sc in J['scopes'] if sc['err']],
         'failed_funcinfo': ['%s:%s' % (f['addr'], f['err']) for f in J['failed_funcinfo']]}
    return {k: sorted(x) for k, x in v.items() if x}


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
    """-> (universe, uncertified list, why, violations {image: {class: [items]}})"""
    try:
        ent, nimg = load_pack(pack)
    except (OSError, ValueError, KeyError) as e:
        return 0, [], 'pack unreadable: %s' % e, {}
    if not os.path.isdir(root): return 0, [], 'no EHFACTS directory', {}
    fs = facts(root); unc = []; red = {}; uni = 0; spans = []; code = sorted(a for a, k in ent.items() if k == 'code')
    if 'exe' not in fs: return 0, [], 'no ehtables.json for the exe', {}
    if not code: return 0, [], 'the pack has no code entry', {}
    if len(fs) != nimg: return 0, [], '%d facts files for %d images of the pack' % (len(fs), nimg), {}
    for img, p in sorted(fs.items()):
        try:
            with open(p) as fh: J = json.load(fh)
            it = items(J); lo, hi = (hx(x) for x in J['text']); vi = violations(J)
            if vi: red[img] = vi
        except (OSError, ValueError, KeyError, TypeError, Bad) as e:
            return 0, [], '%s: ehtables.json unusable: %s' % (img, e), red
        spans.append((lo, hi, img)); uni += len(it)
        for t, f, v in it:
            if ent.get(v) != 'code': unc.append('%s:%08x:%s:%08x' % (img, t, f, v))
    orphan = [a for a in code if sum(1 for lo, hi, _ in spans if lo <= a < hi) != 1]
    if orphan: return uni, sorted(unc), '%d code entries of the pack lie in no image span or in several (first %08x): an image without facts' % (len(orphan), orphan[0]), red
    return uni, sorted(unc), '', red


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
    import cli
    (pack, root), _ = cli.parse('usage: gate_eh.py PACK ROOT   exception-handling gate', argv, (2, 2))
    uni, unc, why, red = check(pack, root)
    nred = sum(len(x) for v in red.values() for x in v.values())
    if why: status, detail = 'red', why
    elif nred or unc:
        status = 'red'; detail = '%d red EH facts, %d of %d table targets are no lifted entry' % (nred, len(unc), uni)
    else: status, detail = 'green', '%d of %d table targets are lifted entries, no red EH fact' % (uni, uni)
    res = {'format': 'fnvnx-gate/1', 'name': 'eh', 'status': status, 'universe': uni, 'certified': uni - len(unc), 'uncertified': unc[:50],
           'violations': {i: {k: {'count': len(x), 'first': x[:20]} for k, x in v.items()} for i, v in sorted(red.items())}, 'detail': detail}
    with open(os.path.join(os.environ['GATE_OUT'], 'eh.json'), 'w') as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == 'green' else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
