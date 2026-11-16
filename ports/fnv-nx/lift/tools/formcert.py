#!/usr/bin/env python3
"""Form census and certificate lookup (R2). One form = fnvlift/formkey.py's form key `mnemonic|operand classes|opcode bytes`.
    census(insns)   -> Counter of form keys of the decoded instructions of one entry (capstone instructions as core.explore returns them)
    Certs(dir)      -> .status(form) 'sail' | 'none'
Certificates are the JSON files written by proof/prove.py under the directory passed as `d`: {"form": key, "result": "proved"|"FAILED..."|"model-gap"|"timeout"..., ...}.
A form is certified when it has at least one certificate and EVERY certificate of it says "proved", or carries "accepted": true (the only way a refines-model / model-defect
status counts). Anything else (no file, a failed, gap or timed-out encoding) is `none`; an unreadable file stops the build. A missing directory certifies nothing (`present` False)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import collections, json, os, sys


def form_key(ins):
    from capstone import x86 as X
    from fnvnx.lift.fnvlift.formkey import form_key as fk
    return fk(ins, X)[0]


def census(insns):
    return collections.Counter(form_key(i) for i in insns.values())


class Certs:
    def __init__(self, d):
        self.present = os.path.isdir(d)
        ok = {}
        for f in sorted(os.listdir(d)) if self.present else ():
            if not f.endswith('.json'):
                continue
            j = json.load(open(os.path.join(d, f)))           # an unreadable certificate file is an error of the build, not an uncertified form
            good = j['result'] == 'proved' or j.get('accepted') is True
            ok[j['form']] = ok.get(j['form'], True) and good
        self.ok = ok

    def status(self, form):
        return 'sail' if self.ok.get(form) else 'none'


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fnvnx.lift.tools.cli as cli
    (*cd,), _ = cli.parse('usage: formcert.py [CERTDIR]   count the certified forms of CERTDIR', sys.argv[1:], (1, 1))
    c = Certs(*cd)
    print('certificate dir present: %s; forms with a certificate file %d, certified %d' % (c.present, len(c.ok), sum(c.ok.values())))
