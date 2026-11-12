#!/usr/bin/env python3
"""gate_reach - certificates that a branch of a platform import body is unreachable (L1 CONTRACT, gate `reach`).

    gate_reach.py --requests reach_requests.tsv --gpa gpa_names.tsv --image NAME=pe,ranges.tsv ... [--bridge bridge.tsv]
                  [--out DIR/reach.tsv] [--facts-h reach_facts.h] [--facts-tsv reach.facts.tsv]
    gate_reach.py --objects <ld out>/inputs.rsp --requests reach_requests.tsv --certified reach.facts.tsv [--out reach.link.tsv]

Why.  A request that a Win32 import accepts by its documentation but the target cannot express leads, in the body of the platform
import, to `fnvnx_reach_violation(fact_id)` (loader/include/fnvnx_reach.h).  The build is valid only if that call is unreachable
for exactly the linked set of images.  Every fact_id has a certificate from this gate, produced from the machine code of the
images; a referenced fact without a certificate makes the build red.  No hand-written list: the requests are data of the platform
manifest (manifest/reach_requests.tsv, columns request dll name arg_index kind values note) and the images are an explicit --image
list of the stage.

Request kinds (the TSV `values` column; `note` is free text and is not read):
  imm_set    `0xA,0xB,...`  (separators , | space)  stdcall argument arg_index of every call of dll!name is one of the values or not in them: forbidden set
  imm_mask   `0xM`          (argument & M) == 0 for every value of the argument
  result_of  `callee=d!n|d!n; argI [& M] == V; argI [& M] != V; ...`     (the platform may spell the kind handle_origin: accepted,
             same grammar and meaning; argflow.handle_origin is the engine.)  The argument arg_index "is, on all paths, the result
             of a call of an import in the callee set whose own stdcall arguments satisfy every clause": argI is the callee's
             stdcall argument I (0 = pushed last), the clause holds when (argI & M) op V for each of its derived immediate values
             (M omitted = all bits).  The callee set is dll!name pairs (dll normalised: lowercase, .dll appended).  A failed call
             returns a value that is not a handle: that case is the business of the immediate-value facts about the same argument.
             A further clause `ret != V on every path to the site` (V a number): on every control-flow path from that call to the
             site the result was compared with V by a still-valid copy and the path crossed the `!= V` edge of the deciding branch
             (argflow.result_predicate: cmp / test / inc idioms, flags not redefined; anything else is `ret-predicate` open).
Numbers: C syntax (0x..., decimal), 32-bit.  A row that does not parse is an error (exit 2), never skipped.

Fact ids: index (from 1) of the request in the canonical order (dll, name, arg_index, kind, canonical values) over the request list;
the table id is the sha256 of the canonical request list.  Both are functions of the requests alone.

Rule.  A request about import I = (dll D, name N, argument k) is CERTIFIED for the image set S iff
 (a) every static use of I's slot in every image of S has argument k derived CLOSED on all paths by argflow (derive_arg; the uses:
     imports_called: call [slot], thunks, register-loaded calls) and every derived value satisfies the predicate; an escape of
     the slot value (imports_called's open list) or an import of D by ordinal (it could be I) is not certified.  A leaf that is not
     an immediate (string, ret, import slot) is not an immediate: for imm_set / imm_mask it is not certified; for result_of the
     value is the eax result of a call, which must be a call of an import of the callee set (handle_origin, mode `strict`:
     a call between the store and the load of the handle breaks the chain), whose arguments are derived the same way;
 (b) I is not obtainable dynamically: every GetProcAddress site of every image of S (gpa_names.tsv; every static reference to a
     GetProcAddress slot must be covered by a row of the table) is closed, and no pair (D, N), (*, N) (module unknown) or (D, #n)
     / (*, #n) (an ordinal in D could be N) exists.  An OPEN site (name or module not derived) anywhere in S makes (b) fail for
     EVERY request: reason `gpa-open`, the condition under which certificates are not valid yet;
 (c) the assumptions of argflow A1..A6 (and A8, the provenance premise of memfx, when its rule closed a handle chain) are recorded in each certificate (BASE: the contract's standing assumptions, the same for
     every gate that uses argflow); an assumption that is not a proven rule makes the fact NOT certified: BLOCKING = {A7} (a call
     between a store and a load does not write that memory; only the `open` handle mode depends on it).  A fact whose only
     derivation needs A7 is listed `assumption:A7`.
Proof argument: (a) says that the argument at every call that can execute is in the set the predicate admits (argflow's walk covers
all paths of the function backwards, expands own parameters to every caller, and is closed or reports a break); (b) says that no
call can execute that is not a static use of the slot (the only way to get an import's address other than its slot is
GetProcAddress, G1; a forwarder chain between distinct dll names is not followed, G2); therefore no execution passes a value
outside the predicate to the import, and the branch of the body that handles it is unreachable.  Assumptions: A1..A6, G1, G2, and
that the facts' ranges contain every executed instruction of the images (A4).  The certificate fixes the set of images by hash
(sha256 of the PE and of the ranges file), the request, the GetProcAddress rows used, the assumptions and the per-site proof chain.

Outputs ($GATE_OUT, gatefile.py): reach.json (gate `reach`; universe = facts, certified = certified facts; uncertified ids
`<fact_id>:<reason>:<image>:<address>`, reason `assumption:A7` etc. may contain a colon: split the last two fields off), reach.ids
(all of them, sorted), reach.facts.tsv (`fact_id<TAB>sha256 of the certificate`, certified facts only, sorted by id).  --out: per
fact status, sites examined and reasons.  --facts-h: the C header (FNVNX_REACH_<DLL>_<NAME>_<n> with n the 1-based position among
the requests of the same dll!name in canonical order; FNVNX_<request id> for the platform's own names; FNVNX_REACH_TABLE_ID).

Mode --objects (gate `reachlink`, the link side).  The universe is every reference to the ELF symbol fnvnx_reach_violation in the
objects and archive members of the response file (one quoted path per line, ld.py's inputs.rsp), read from the relocation
tables (sh_info of a SHT_RELA / SHT_REL names the section; no text).  A reference is green iff
  - it is a R_AARCH64_P32_CALL26 / JUMP26 in an executable section (any other reference, e.g. a pointer in data, lets w0 be
    anything: `reference-not-call`);
  - w0 is set at the call by an immediate move (mov / movz, followed in program order by movk) in the same basic block: walking
    backwards from the call every instruction before the move is a non-branch, no instruction between the move and the call (the
    call included) is a branch target, a symbol, or the target of a relocation, and the last write of w0 before the call is the move (`no-immediate`, `block-boundary`, `label`).  Decoded with capstone ARM64;
  - the function that contains the call has no indirect jump (`br`: its tables hide branch targets: `indirect-jump`);
  - the immediate is a fact id of the table and has a certificate (`fact-not-in-table`, `fact-not-certified`).
Red ids `member:section+offset:reason`; gate `reachlink` (universe = references, certified = green ones).
Exe-derived: container and fnv-nx-build only.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'link'))
import argflow
import gatefile
import memfx
import gateids
import gpa_names
import iat
import relocs

BASE_ASSUMPTIONS = ('A1', 'A2', 'A3', 'A4', 'A5', 'A6')
BLOCKING = ('A7',)
COLUMNS = ['request', 'dll', 'name', 'arg_index', 'kind', 'values', 'note']
KINDS = {'imm_set': 'imm_set', 'imm_mask': 'imm_mask', 'result_of': 'result_of', 'handle_origin': 'result_of'}
SYMBOL = 'fnvnx_reach_violation'
M32 = 0xffffffff


class FormatError(Exception):
    pass


# ---------------------------------------------------------------- requests
def num(s):
    try:
        return int(s.strip(), 0) & M32
    except ValueError:
        raise FormatError('not a number: %r' % s)


def parse_clause(s):
    """`argI [& M] == V` or `argI [& M] != V` -> [I, M (all bits = 0xffffffff), op, V]."""
    s = s.strip()
    if not s.startswith('arg'):
        raise FormatError('clause %r does not start with argI' % s)
    op = '==' if '==' in s else '!=' if '!=' in s else None
    if not op:
        raise FormatError('clause %r has no == or !=' % s)
    lhs, rhs = s.split(op, 1)
    lhs = lhs[3:]
    if '&' in lhs:
        i, m = lhs.split('&', 1)
        mask = num(m)
    else:
        i, mask = lhs, M32
    return [int(i.strip(), 10), mask, op, num(rhs)]


def parse_values(kind, text):
    if kind == 'imm_set':
        vals = [x for x in text.replace(',', ' ').replace('|', ' ').split() if x]
        if not vals:
            raise FormatError('imm_set without values')
        return {'forbidden': sorted({num(x) for x in vals})}
    if kind == 'imm_mask':
        return {'mask': num(text)}
    parts = [p.strip() for p in text.split(';') if p.strip()]
    if not parts or not parts[0].startswith('callee='):
        raise FormatError('result_of values must start with callee=')
    callee = []
    for c in parts[0][len('callee='):].split('|'):
        d, n = c.strip().split('!', 1)
        callee.append([iat.norm_dll(d), n])
    spec = {'callee': sorted(callee), 'where': sorted(parse_clause(p) for p in parts[1:] if not p.startswith('ret'))}
    rets = [parse_ret(p) for p in parts[1:] if p.startswith('ret')]
    if rets:
        spec['ret'] = sorted(rets)
    return spec


def parse_ret(s):
    """`ret != V on every path to the site` -> ['!=', V]: on every control-flow path from the call to the site the result was compared
    with V and the `!= V` edge taken (argflow.result_predicate)."""
    t = s.replace('!=', ' != ').split()
    if len(t) != 9 or t[:2] != ['ret', '!='] or t[3:] != ['on', 'every', 'path', 'to', 'the', 'site']:
        raise FormatError('clause %r is not `ret != V on every path to the site`' % s)
    return ['!=', num(t[2])]


def parse_requests(text):
    """-> canonical sorted list of dicts(request, dll, name, k, kind, spec, note); fact id = index + 1."""
    lines = [l for l in text.split('\n') if l.strip()]
    if not lines or lines[0].split('\t') != COLUMNS:
        raise FormatError('header must be %s' % '\t'.join(COLUMNS))
    out = []
    for l in lines[1:]:
        f = l.split('\t')
        if len(f) < 6:
            raise FormatError('row with %d columns: %r' % (len(f), l[:60]))
        if f[4] not in KINDS:
            raise FormatError('unknown kind %r' % f[4])
        kind = KINDS[f[4]]
        out.append(dict(request=f[0], dll=iat.norm_dll(f[1]), name=f[2], k=int(f[3], 10), kind=kind,
                        spec=parse_values(kind, f[5]), note=f[6] if len(f) > 6 else ''))
    out.sort(key=lambda r: (r['dll'], r['name'], r['k'], r['kind'], canon(r['spec'])))
    for i in range(1, len(out)):
        if (out[i]['dll'], out[i]['name'], out[i]['k'], out[i]['kind'], canon(out[i]['spec'])) == \
                (out[i - 1]['dll'], out[i - 1]['name'], out[i - 1]['k'], out[i - 1]['kind'], canon(out[i - 1]['spec'])):
            raise FormatError('duplicate request %s / %s' % (out[i - 1]['request'], out[i]['request']))
    for i, r in enumerate(out):
        r['id'] = i + 1
    return out


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(',', ':'))


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def table_id(reqs):
    return sha(canon([[r['id'], r['dll'], r['name'], r['k'], r['kind'], r['spec']] for r in reqs]))


# ---------------------------------------------------------------- GetProcAddress rows
def read_gpa(text):
    """gpa_names.tsv -> [dict(module, site, status, pairs [(dll, name)])]."""
    rows = []
    for l in text.split('\n'):
        f = l.split('\t')
        if len(f) < 12 or f[0] == 'module':
            continue
        pairs = [tuple(p.split('!', 1)) for p in f[8].split(';') if p]
        rows.append(dict(module=f[0].lower(), site=int(f[1], 16), status=f[4], pairs=sorted(pairs)))
    return rows


def gpa_state(images, rows):
    """(global reasons [(reason, image, addr)], closed pairs [(image, site, dll, name)], digest of the rows of S)."""
    names = sorted(images)
    mine = [r for r in rows if r['module'] in images]
    reasons, covered = [], {(r['module'], r['site']) for r in mine}
    for r in sorted(mine, key=lambda r: (r['module'], r['site'])):
        if r['status'] != 'closed':
            reasons.append(('gpa-open', r['module'], '0x%x' % r['site']))
    for nm in names:
        img = images[nm]
        slots = {s for s, (d, n) in img.imports.items() if n == 'GetProcAddress'}
        for ref in sorted(r for r in img.relocs if img.word(r) in slots):
            if (nm, ref) not in covered:
                reasons.append(('gpa-uncovered', nm, '0x%x' % ref))
        for s in sorted(img.imports):
            d, n = img.imports[s]
            if n.startswith('#') and iat.norm_dll(d) == 'kernel32.dll':
                reasons.append(('gpa-ordinal-import', nm, '0x%x' % s))
    pairs = sorted((r['module'], r['site'], d, n) for r in mine if r['status'] == 'closed' for d, n in r['pairs'])
    dig = sha(canon([[r['module'], r['site'], r['status'], r['pairs']] for r in sorted(mine, key=lambda r: (r['module'], r['site']))]))
    return sorted(set(reasons)), pairs, dig


# ---------------------------------------------------------------- one request
def hexchain(chain):
    return ['%x' % c if isinstance(c, int) else (str(c) if not isinstance(c, tuple) else ':'.join(
        '%x' % x if isinstance(x, int) else str(x) for x in c)) for c in chain]


def leaves_json(res):
    return [[k, (list(v) if isinstance(v, tuple) else v), hexchain(ch)] for k, v, ch in res.sorted_leaves()]


def imm_values(res):
    """(values, bad leaf kinds): the immediate values of a closed Result; any other leaf kind is not an immediate."""
    vals, bad = [], []
    for k, v, ch in res.sorted_leaves():
        if k in ('imm', 'ord'):
            vals.append(v & M32)
        else:
            bad.append(k)
    return sorted(set(vals)), sorted(set(bad))


def holds(v, mask, op, want):
    return ((v & mask) == want) if op == '==' else ((v & mask) != want)


class Fact:
    def __init__(self, req):
        self.req = req
        self.reasons = set()          # (reason, image, address)
        self.detail = {}              # (reason, image, address) -> text
        self.sites = []               # certificate material
        self.examined = 0
        self.assumptions = set()      # beyond BASE_ASSUMPTIONS: A8 when the may-write rule (memfx) closed a chain

    def fail(self, reason, image, addr, detail=''):
        key = (reason, image, addr)
        self.reasons.add(key)
        if detail:
            self.detail[key] = detail[:300]


def check_imm(f, nm, img, call):
    req = f.req
    res = argflow.derive_arg(img, call, req['k'])
    site = '0x%x' % call
    if not res.closed:
        f.fail('arg-open', nm, site, ';'.join('%x %s' % b for b in sorted(res.breaks)))
        return
    vals, bad = imm_values(res)
    f.sites.append(dict(image=nm, call='%x' % call, leaves=leaves_json(res)))
    if bad:
        f.fail('not-immediate', nm, site, 'leaf kinds %s' % ','.join(bad))
        return
    if req['kind'] == 'imm_set':
        hit = sorted(set(vals) & set(req['spec']['forbidden']))
        if hit:
            f.fail('forbidden-immediate', nm, site, ','.join('0x%x' % v for v in hit))
    else:
        hit = sorted(v for v in vals if v & req['spec']['mask'])
        if hit:
            f.fail('forbidden-bits', nm, site, ','.join('0x%x' % v for v in hit))


def check_result_of(f, nm, img, call):
    req = f.req
    site = '0x%x' % call
    mode, (ok, origins, brk) = 'strict', argflow.handle_origin(img, call, req['k'], 'strict')
    if not ok and img.mx is not None:
        mode, (ok, origins, brk) = 'rule', argflow.handle_origin(img, call, req['k'], 'rule')
        if ok:
            f.assumptions.add('A8')
    if not ok:
        ok2, origins2, brk2 = argflow.handle_origin(img, call, req['k'], 'open')
        if ok2:
            f.fail('assumption:A7', nm, site, 'the chain is closed only if a call between store and load does not write the handle memory; '
                   'strict breaks: ' + ';'.join('%x %s' % b for b in brk[:4]))
            mode, origins = 'open', origins2
        else:
            f.fail('handle-open', nm, site, ';'.join('%x %s' % b for b in brk[:4]))
            return
    callee = {tuple(c) for c in req['spec']['callee']}
    out = []
    for c, va, chain in origins:
        entry = dict(call='%x' % va, callee=[str(x) for x in c[1:]] if c[0] == 'import' else [c[0]], chain=hexchain(chain), args={})
        out.append(entry)
        if c[0] != 'import' or (iat.norm_dll(c[1]), c[2]) not in callee:
            f.fail('origin-not-in-set', nm, '0x%x' % va, 'the handle is the result of %r' % (c,))
            continue
        for i, mask, op, want in req['spec']['where']:
            r = argflow.derive_arg(img, va, i)
            if not r.closed:
                f.fail('origin-arg-open', nm, '0x%x' % va, 'arg%d: %s' % (i, ';'.join('%x %s' % b for b in sorted(r.breaks))))
                continue
            vals, bad = imm_values(r)
            entry['args'][str(i)] = leaves_json(r)
            if bad:
                f.fail('origin-arg-not-immediate', nm, '0x%x' % va, 'arg%d leaf kinds %s' % (i, ','.join(bad)))
            for v in vals:
                if not holds(v, mask, op, want):
                    f.fail('origin-violates', nm, '0x%x' % va, 'arg%d = 0x%x violates (& 0x%x) %s 0x%x' % (i, v, mask, op, want))
    rp = []
    for op, want in req['spec'].get('ret', []):
        r = argflow.result_predicate(img, call, req['k'], callee, op, want)
        if not r.closed:
            f.fail('ret-predicate', nm, site, 'result %s 0x%x on every path: %s' % (op, want, ';'.join('%x %s' % b for b in sorted(r.breaks)[:3])))
        rp.append(dict(relation=op, value=want, chain=[hexchain(c) for k2, v2, c in r.sorted_leaves()], ok=r.closed))
    f.sites.append(dict(image=nm, call='%x' % call, mode=mode, origins=out, **({'ret': rp} if rp else {})))


def check_request(req, images, gpa_global, gpa_pairs):
    f = Fact(req)
    for nm in sorted(images):
        img = images[nm]
        slots = sorted(s for s, (d, n) in img.imports.items() if n == req['name'] and iat.norm_dll(d) == req['dll'])
        for s in sorted(img.imports):
            d, n = img.imports[s]
            if n.startswith('#') and iat.norm_dll(d) == req['dll']:
                f.fail('ordinal-import', nm, '0x%x' % s, 'import by ordinal from %s' % d)
        if not slots:
            continue
        calls, open_ = argflow.imports_called(img, req['name'])
        for va, why in open_:
            f.fail('slot-escape', nm, '0x%x' % va, why)
        for call in calls:
            f.examined += 1
            if req['kind'] == 'result_of':
                check_result_of(f, nm, img, call)
            else:
                check_imm(f, nm, img, call)
    for reason, nm, addr in gpa_global:
        f.fail(reason, nm, addr)
    for nm, site, d, n in gpa_pairs:
        if n == req['name'] and d in (req['dll'], '*'):
            f.fail('gpa-pair', nm, '0x%x' % site, '%s!%s' % (d, n))
        elif n.startswith('#') and d in (req['dll'], '*'):
            f.fail('gpa-ordinal', nm, '0x%x' % site, '%s!%s' % (d, n))
    return f


def certificate(f, imgmeta, gpa_digest):
    return canon(dict(request=dict(id=f.req['id'], request=f.req['request'], dll=f.req['dll'], name=f.req['name'], k=f.req['k'],
                                  kind=f.req['kind'], spec=f.req['spec']),
                      images=imgmeta, gpa=gpa_digest, assumptions=list(BASE_ASSUMPTIONS) + sorted(f.assumptions), sites=sorted(f.sites, key=canon)))


def run(reqs, images, imgmeta, gpa_rows):
    """-> (facts [Fact], certificates {id: sha256}); a fact is certified iff it has no reason."""
    g, pairs, dig = gpa_state(images, gpa_rows)
    facts, certs = [], {}
    for r in reqs:
        f = check_request(r, images, g, pairs)
        facts.append(f)
        if not f.reasons:
            certs[r['id']] = sha(certificate(f, imgmeta, dig))
    return facts, certs, g


# ---------------------------------------------------------------- outputs
def uncertified_ids(facts):
    return sorted('%d:%s:%s:%s' % ((f.req['id'],) + r) for f in facts for r in sorted(f.reasons))


def macro_part(s):
    return ''.join(c if c.isalnum() else '_' for c in s)


def header(reqs):
    tid = table_id(reqs)
    lines = ['/* reach_facts.h - generated by contract/gate_reach.py from the request table; do not edit.  Exe-derived ids: container only. */',
             '#ifndef FNVNX_REACH_FACTS_H', '#define FNVNX_REACH_FACTS_H',
             '#define FNVNX_REACH_TABLE_ID "%s"' % tid,
             'static const char fnvnx_reach_table_id[] = FNVNX_REACH_TABLE_ID;',
             '#define FNVNX_REACH_FACT_COUNT %d' % len(reqs)]
    seq = {}
    for r in reqs:
        key = (r['dll'], r['name'])
        seq[key] = seq.get(key, 0) + 1
        dll = r['dll'][:-4] if r['dll'].endswith('.dll') else r['dll']
        lines.append('#define FNVNX_REACH_%s_%s_%d %d   /* %s: arg %d %s */' % (
            macro_part(dll).upper(), macro_part(r['name']), seq[key], r['id'], r['request'], r['k'], r['kind']))
        pre = r['request'] if r['request'].startswith('REACH_') else 'REACH_' + r['request']
        lines.append('#define FNVNX_%s %d' % (macro_part(pre), r['id']))
    lines.append('#endif')
    return '\n'.join(lines) + '\n'


def report_tsv(facts, certs):
    lines = ['fact_id\trequest\tdll!name\targ\tkind\tstatus\tsites\treasons']
    for f in facts:
        r = f.req
        rs = ['%s@%s:%s%s' % (a, b, c, ' (%s)' % f.detail[(a, b, c)] if (a, b, c) in f.detail else '') for a, b, c in sorted(f.reasons)]
        lines.append('\t'.join([str(r['id']), r['request'], '%s!%s' % (r['dll'], r['name']), str(r['k']), r['kind'],
                                'certified' if r['id'] in certs else 'uncertified', str(f.examined), ' | '.join(rs) or '-']))
    return '\n'.join(lines) + '\n'


def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', newline='\n') as fh:
        fh.write(text)


def gate(facts, certs, g, nimages):
    unc = uncertified_ids(facts)
    uf = sum(1 for f in facts if f.reasons)
    open_note = ('GetProcAddress is open in the image set (%d sites/refs): certificates are not valid yet' % len(g)) if g else \
        'GetProcAddress closed over the image set'
    detail = '%d facts, %d certified over %d images; %s' % (len(facts), len(facts) - uf, nimages, open_note)
    gatefile.write('reach', len(facts), len(facts) - uf, unc, detail)
    gateids.write_ids('reach', unc)
    out = os.environ.get('GATE_OUT')
    if out:
        write(os.path.join(out, 'reach.facts.tsv'), ''.join('%d\t%s\n' % (i, certs[i]) for i in sorted(certs)))
    return uf, detail


# ---------------------------------------------------------------- link side
SHF_EXEC = 4
R_JUMP26, R_CALL26 = 20, 21


def elf_parse(label, d):
    """(sections [(name, type, flags, offset, size, link, info, entsize)], symbols [(name, value, size, type, shndx)], rela
    [(target section index, offset, type, symbol index, addend)]) of an ELF32 LE relocatable."""
    if d[:4] != b'\x7fELF' or d[4] != 1 or d[5] != 1:
        raise ValueError('%s: not ELF32 little-endian' % label)
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 46)
    raw = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    secs = [(relocs.cstr(d, raw[shstrndx][4] + s[0]), s[1], s[2], s[4], s[5], s[6], s[7], s[9]) for s in raw]
    syms, rel = {}, []
    for i, s in enumerate(secs):
        if s[1] == relocs.SHT_SYMTAB:
            stroff = secs[s[5]][3]
            tab = []
            for o in range(s[3], s[3] + s[4], s[7]):
                name, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', d, o)
                tab.append((relocs.cstr(d, stroff + name), value, size, info & 15, shndx))
            syms[i] = tab
    sym = next(iter(syms.values())) if syms else []
    for s in secs:
        if s[1] in (relocs.SHT_RELA, relocs.SHT_REL):
            esz = 12 if s[1] == relocs.SHT_RELA else 8
            for o in range(s[3], s[3] + s[4], esz):
                off, info = struct.unpack_from('<II', d, o)
                add = struct.unpack_from('<i', d, o + 8)[0] if esz == 12 else None
                rel.append((s[6], off, info & 0xff, info >> 8, add))
    return secs, sym, rel


class Code:
    """The decoded instructions of one executable section: labels, indirect jumps."""

    def __init__(self, cs, data, label_offs, reloc_offs):
        self.ins = {}
        self.labels = set(label_offs)
        self.indirect = []
        for off in range(0, len(data) - 3, 4):
            for i in cs.disasm(data[off:off + 4], off, 1):
                self.ins[off] = i
                g = set(i.groups)
                if off in reloc_offs:         # the displacement is a relocation field: the target is the relocation's, not off+0
                    pass
                elif 7 in g and i.operands and i.operands[-1].type == 2:      # BRANCH_RELATIVE
                    self.labels.add(i.operands[-1].imm)
                elif i.mnemonic == 'adr' and i.operands and i.operands[-1].type == 2:
                    self.labels.add(i.operands[-1].imm)
                elif 1 in g and 7 not in g and i.mnemonic not in ('ret',):    # JUMP, not relative: br
                    self.indirect.append(off)


def branches(i):
    return bool({1, 2, 3, 7} & set(i.groups))


def w0_immediate(code, off):
    """The value w0 holds at the call at offset off, or (None, reason)."""
    if off in code.labels:
        return None, 'label'
    p, movks = off - 4, []
    while True:
        i = code.ins.get(p)
        if i is None:
            return None, 'no-immediate'
        if branches(i):
            return None, 'block-boundary'
        _, w = i.regs_access()
        wn = {i.reg_name(x) for x in w}
        if wn & {'w0', 'x0'}:
            op = i.operands[1] if len(i.operands) > 1 else None
            sh = op.shift.value if op is not None and op.shift.type else 0
            if i.mnemonic == 'movk' and op is not None and op.type == 2:
                movks.append((sh, op.imm & 0xffff))
            elif i.mnemonic in ('mov', 'movz') and op is not None and op.type == 2 and len(i.operands) == 2:
                v = (op.imm << sh) & M32
                for s, imm in reversed(movks):
                    v = (v & ~(0xffff << s) & M32) | (imm << s)
                return v, ''
            else:
                return None, 'no-immediate'
        if p in code.labels:
            return None, 'label'
        p -= 4
        if p < 0:
            return None, 'no-immediate'


def function_of(syms, shndx, off):
    for name, value, size, typ, sx in syms:
        if typ == 2 and sx == shndx and size and value <= off < value + size:
            return value, value + size
    return None


def link_check(paths, nids, certified):
    """-> (universe, [red ids]) over the objects and archive members `paths`."""
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.detail = True
    uni, red = 0, []
    for p in paths:
        for label, d in relocs.files(p):
            secs, syms, rel = elf_parse(label, d)
            hits = [(t, o, ty, add) for t, o, ty, si, add in rel if si < len(syms) and syms[si][0] == SYMBOL]
            if not hits:
                continue
            labs = {}
            for t, o, ty, si, add in rel:
                if si < len(syms):
                    nm, value, size, st, shndx = syms[si]
                    if 0 < shndx < len(secs):
                        labs.setdefault(shndx, set()).add((value if st != 3 else 0) + (add or 0))
            for i, (nm, value, size, st, shndx) in enumerate(syms):
                if 0 < shndx < len(secs) and st != 4:
                    labs.setdefault(shndx, set()).add(value)
            cache = {}
            for t, o, ty, add in sorted(hits):
                uni += 1
                rid = '%s:%s+%08x' % (label, secs[t][0], o)
                if not secs[t][2] & SHF_EXEC or ty not in (R_CALL26, R_JUMP26):
                    red.append(rid + ':reference-not-call')
                    continue
                if t not in cache:
                    sec = secs[t]
                    cache[t] = Code(cs, d[sec[3]:sec[3] + sec[4]], labs.get(t, ()), {r[1] for r in rel if r[0] == t})
                code = cache[t]
                v, why = w0_immediate(code, o)
                if v is None:
                    red.append('%s:%s' % (rid, why))
                    continue
                fn = function_of(syms, t, o)
                if any(fn is None or fn[0] <= x < fn[1] for x in code.indirect):
                    red.append(rid + ':indirect-jump')
                elif not 1 <= v <= nids:
                    red.append(rid + ':fact-not-in-table')
                elif v not in certified:
                    red.append(rid + ':fact-not-certified')
    return uni, sorted(red)


def main_objects(a):
    with open(a.requests) as fh:
        nids = len(parse_requests(fh.read()))
    with open(a.certified) as fh:
        certified = {int(l.split('\t')[0]) for l in fh.read().split('\n') if l.strip()}
    paths = []
    with open(a.objects) as fh:
        paths += [ln.strip().strip('"') for ln in fh if ln.strip()]
    uni, red = link_check(paths, nids, certified)
    detail = '%s references: %d of %d red over %d files' % (SYMBOL, len(red), uni, len(paths))
    gatefile.write('reachlink', uni, uni - len(red), red, detail)
    gateids.write_ids('reachlink', red)
    if a.out:
        write(a.out, ''.join(r + '\n' for r in red))
    print(detail)
    return 1 if red else 0


def load_images(specs, stacks, info=None):
    images, meta = {}, []
    for spec in specs:
        nm, rest = spec.split('=', 1)
        pe, rg = rest.split(',')
        with open(pe, 'rb') as fh:
            pb = fh.read()
        with open(rg, 'rb') as fh:
            rb = fh.read()
        img = argflow.Image(pb, nm.lower(), argflow.read_ranges(rb.decode()))
        img.import_stack = stacks
        if info is not None:
            memfx.Memfx(img, info)
        images[nm.lower()] = img
        meta.append(dict(name=nm.lower(), pe=sha(pb), ranges=sha(rb)))
    return images, sorted(meta, key=lambda m: m['name'])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--requests', required=True)
    ap.add_argument('--gpa')
    ap.add_argument('--image', action='append', default=[], help='name=pe_path,ranges_path (the image set of the stage)')
    ap.add_argument('--images-file', help='images.tsv of loadlib.py (instead of --image)')
    ap.add_argument('--bridge')
    ap.add_argument('--escape', action='append', default=[], help='import_escape.tsv of a platform backend (repeat; worst letter counts)')
    ap.add_argument('--writes', help='per import argument write facts (memfx.ImportInfo); default: every pointer argument may be written')
    ap.add_argument('--out')
    ap.add_argument('--facts-h')
    ap.add_argument('--facts-tsv')
    ap.add_argument('--objects')
    ap.add_argument('--certified')
    a = ap.parse_args(argv)
    if a.images_file:
        a.image = a.image + gpa_names.specs_of(a.images_file)
    try:
        if a.objects:
            if not a.certified:
                ap.error('--objects needs --certified')
            return main_objects(a)
        if not a.gpa or not a.image:
            ap.error('--gpa and --image are required')
        with open(a.requests) as fh:
            reqs = parse_requests(fh.read())
    except FormatError as e:
        print('error: %s' % e, file=sys.stderr)
        return 2
    stacks = {}
    if a.bridge:
        with open(a.bridge) as fh:
            stacks = gpa_names.import_stacks(fh.read())
    info = None
    if a.bridge:
        with open(a.bridge) as fh:
            bt = fh.read()
        info = memfx.ImportInfo(bt, [open(p).read() for p in a.escape], open(a.writes).read() if a.writes else '')
    images, meta = load_images(a.image, stacks, info)
    with open(a.gpa) as fh:
        rows = read_gpa(fh.read())
    facts, certs, g = run(reqs, images, meta, rows)
    if a.out:
        write(a.out, report_tsv(facts, certs))
    if a.facts_h:
        write(a.facts_h, header(reqs))
    if a.facts_tsv:
        write(a.facts_tsv, ''.join('%d\t%s\n' % (i, certs[i]) for i in sorted(certs)))
    uf, detail = gate(facts, certs, g, len(images))
    print(detail)
    for f in facts:
        print('fact %d %s: %s' % (f.req['id'], f.req['request'], 'certified' if f.req['id'] in certs else
                                  'not certified: ' + ', '.join(sorted({r[0] for r in f.reasons}))))
    return 1 if uf else 0


if __name__ == '__main__':
    sys.exit(main())
