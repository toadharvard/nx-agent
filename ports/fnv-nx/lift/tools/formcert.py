#!/usr/bin/env python3
"""Form census and certificate lookup (R2). One form = fnvlift/formkey.py's form key `mnemonic|operand classes|opcode bytes`.
    census(insns)   -> Counter of form keys of the decoded instructions of one entry (capstone instructions as core.explore returns them)
    Certs(dir)      -> .status(form) 'sail' | 'none'
Certificates are the JSON files written by proof/prove.py under FNVNX_PROOF (default /home/user/l3work/proof): {"form": key, "result": "proved"|"FAILED..."|"model-gap"|"timeout"..., ...}.
A form is certified when it has at least one certificate and EVERY certificate of it says "proved", or carries "accepted": true (the only way a refines-model / model-defect
status counts). Anything else (no file, a failed, gap or timed-out encoding) is `none`; an unreadable file stops the build. A missing directory certifies nothing (`present` False)."""
import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # LIFT first: fnvlift is the package lift/fnvlift
PROOF = os.environ.get('FNVNX_PROOF', '/home/user/l3work/proof')


def form_key(ins):
    from capstone import x86 as X
    from fnvlift.formkey import form_key as fk
    return fk(ins, X)[0]


def census(insns):
    return collections.Counter(form_key(i) for i in insns.values())


class Certs:
    def __init__(self, d=PROOF):
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
    c = Certs(*sys.argv[1:2])
    print('certificate dir present: %s; forms with a certificate file %d, certified %d' % (c.present, len(c.ok), sum(c.ok.values())))
