#!/usr/bin/env python3
"""Gate "forms" (fnvnx-gate/1), class 6 of the verdict: every instruction form of the lifted pack carries a certificate.
    python3 -I gate_forms.py PACK PROOF        env GATE_OUT (result dir); writes GATE_OUT/forms.json
PACK   the published pack tree (tools/liftgen.py --finish): PACK/forms.tsv lists every form of every lifted entry of
       every image, `form <TAB> count <TAB> status`; the form key is test/instest.py's `mnemonic|operand classes|opcode bytes`.
PROOF  the certificate directory (proof/prove.py output, one JSON per proved encoding).
Universe: the forms of PACK/forms.tsv. Certified: forms tools/formcert.py Certs(PROOF) reports 'sail' (at least one
certificate, every certificate of the form "proved" or accepted). The status column of forms.tsv is not read: the gate
recomputes it from PROOF. A missing PACK/forms.tsv or a missing PROOF directory is red (nothing is certified by absence);
an empty forms.tsv is red too, a pack has entries. uncertified: the first 50 uncertified forms, sorted.
Exit 0 iff the result is green."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv):
    pack, proof = argv
    import formcert
    forms, why = [], ''
    p = os.path.join(pack, 'forms.tsv')
    if not os.path.isfile(p):
        why = 'no forms.tsv in the pack'
    elif not os.path.isdir(proof):
        why = 'no certificate directory'
    else:
        with open(p) as fh:
            forms = sorted({l.split('\t')[0] for l in fh.read().splitlines() if l})
        if not forms:
            why = 'forms.tsv lists no form'
    unc = []
    if not why:
        certs = formcert.Certs(proof)
        unc = [f for f in forms if certs.status(f) != 'sail']
    if why:
        status, detail = 'red', why
    elif unc:
        status, detail = 'red', '%d of %d forms have no certificate' % (len(unc), len(forms))
    else:
        status, detail = 'green', '%d of %d forms certified' % (len(forms), len(forms))
    res = {'format': 'fnvnx-gate/1', 'name': 'forms', 'status': status, 'universe': len(forms), 'certified': len(forms) - len(unc),
           'uncertified': unc[:50], 'detail': detail}
    with open(os.path.join(os.environ['GATE_OUT'], 'forms.json'), 'w') as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == 'green' else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
