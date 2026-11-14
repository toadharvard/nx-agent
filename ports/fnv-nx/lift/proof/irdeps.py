#!/usr/bin/env python3
"""Certificate key by the definitions the model run EXECUTED (K), not by the whole IR.

A certificate used to be valid for exactly one IR sha256, so any model hunk invalidated all of them.  Now a record carries
  model_fns   = the Sail functions isla called while executing the encoding (isla --probe-all, "Calling NAME(" events; model_sail.run_isla)
  closure_sha = sha256 over the normalized text of those functions plus every val / register / struct / enum / union declaration that
                their text names, transitively through the declarations (closure() below)
and is valid for any IR whose closure_sha over the SAME names is unchanged (certs_build).

Soundness (the rule closes the class; no list, no per-hunk decision).  Fix the isla binary, the isla config, the -I / --reset-constraint inputs
and the encoding.  The isla run is a deterministic function of the definitions it evaluates.  It evaluates a function only after a call event, and
it reads a declaration (a register's type, a struct layout, an enum, a union, an extern val) only through a name in the text of an evaluated
function.  Let IR' agree with IR on the normalized text of every function in model_fns and of every declaration closure() reaches from them.
By induction over the steps of the run: step 1 evaluates the entry function (in model_fns, text equal); a step that calls g finds g in
model_fns (it was called in the run on IR) with equal text; every declaration it touches is in the closure, equal.  So the run on IR' is
step for step the run on IR: the same trace, the same model post-state, the same certificate.  A hunk in a function the run never called
cannot change it; a hunk in a called function changes its text, hence closure_sha, hence the shape is requeued.
What would break it (each is part of the key or of the pins, never a decision): a change of the isla binary or its config (isla and the config
are pinned by sail_pins.txt and x86.toml, ASM-009), of the -I inputs or the splice registers (model_sail.SEG, sym_constraint: the run's inputs
are part of the shape key and of model_terms_sha), of the position comments of the IR (stripped by norm(): a splice that moves lines does not change the key),
or an isla primitive ("val NAME = \\"extern\\"": its text is in the closure, the implementation is isla's, pinned).
Differential check (test_irdeps): the closure_sha of an encoding changes under an edit of a function in model_fns and under no edit of any other function."""
import hashlib
import os
import re

POS = re.compile(r' `\d+(?: [\d:\-]+)?(?=;|$)')       # IR position annotation:  `67 2417:4-2492:5   or   `272
TOKEN = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

def norm(text):
    """the text of an IR item without source positions and with single spaces"""
    return re.sub(r'[ \t]+', ' ', POS.sub('', text)).strip()

def encode(name):
    """Sail name (as isla prints it in a call event) -> IR name: z + name with z -> zz, < -> zI, > -> zK (sail_lib z-encoding of the characters that occur)"""
    return 'z' + ''.join({'z': 'zz', '<': 'zI', '>': 'zK'}.get(c, c) for c in name)

class IR:
    """the items of an IR text: fns (name -> text), vals, regs, types (struct / enum / union), each normalized"""
    def __init__(self, text):
        self.fns, self.vals, self.regs, self.types = {}, {}, {}, {}
        cur = None
        for line in text.split('\n'):
            if cur is not None:
                cur[1].append(line)
                if line == '}':
                    self._put(cur[0], cur[2], '\n'.join(cur[1])); cur = None
                continue
            m = re.match(r'(fn|struct|enum|union) (\w+)', line)
            if m:
                cur = (m.group(1), [line], m.group(2)); cur = (cur[0], cur[1], cur[2])
                if line.endswith('}'): self._put(cur[0], cur[2], line); cur = None
                continue
            m = re.match(r'(val|register) (\w+)', line)
            if m: (self.vals if m.group(1) == 'val' else self.regs)[m.group(2)] = norm(line)
        self.ids = None
    def _put(self, kind, name, text):
        (self.fns if kind == 'fn' else self.types)[name] = norm(text)

_cache = {}
def load(path):
    """parsed IR of a file (cached per path and sha: the IR is 10 MB)"""
    data = open(path, 'rb').read(); key = hashlib.sha256(data).hexdigest()
    if key not in _cache: _cache[key] = IR(data.decode())
    return _cache[key]

def resolve(ir, names):
    """isla call names -> the IR items they denote: (found IR names, names that are no IR item: isla builtins, kept literally)"""
    found, other = set(), set()
    for n in names:
        z = encode(n)
        if z in ir.fns or z in ir.vals: found.add(z)
        elif n in ir.fns or n in ir.vals: found.add(n)
        else: other.add(n)
    return sorted(found), sorted(other)

def closure(ir, names):
    """[(kind, name, text)] of the executed functions and the declarations their text names, transitively (a struct names its field types, a union its member types).  A name that is a function of the IR is NOT followed from a text: a function the run never called is not part of the run (it is in the closure only if the run called it, i.e. it is in `names`); an extern val (isla primitive, no fn) is a declaration and is"""
    out, todo, seen = [], list(names), set()
    while todo:
        n = todo.pop()
        if n in seen: continue
        seen.add(n)
        for kind, table in (('fn', ir.fns), ('val', ir.vals), ('register', ir.regs), ('type', ir.types)):
            if n in table:
                out.append((kind, n, table[n]))
                if kind in ('fn', 'type'): todo.extend(t for t in TOKEN.findall(table[n]) if t != n and (t in ir.regs or t in ir.types or (t in ir.vals and t not in ir.fns)))
    return sorted(out)

def closure_sha(ir, names, env=''):
    """sha256 of the closure of the executed functions (`names` = isla call names), of the isla builtins' names and of `env` (the run environment: model_sail.run_env_sha)"""
    found, builtin = resolve(ir, names)
    h = hashlib.sha256()
    for kind, name, text in closure(ir, found): h.update(('%s %s\n%s\n' % (kind, name, text)).encode())
    h.update(('builtins ' + ' '.join(sorted(set(builtin))) + '\nenv ' + env + '\n').encode())
    return h.hexdigest()

def sha_of_file(path, names, env=''):
    return closure_sha(load(path), names, env)
