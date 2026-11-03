#!/usr/bin/env python3
"""BUG-1 reproducer: at HEAD the layout probe and the vtable synthesis (both use tools/link/layout.flags()) compile
without -fsigned-char, which shim/include/fnvnx_compat.h requires with #error since commit 4906b1b, so every probe
fails silently: `fnvlink.py probe` reports 'layouts: 0 known' and synthesizes no vtable.

    python3 repro_bug1_probe_flags.py            # needs SSE2NEON (any dir; only the include path is used)

Prints the probe results with the tool's flags (expected: None / {}), then with CXX=cxx_msvc_semantics.sh (measured).
Hand-written unit, no decomp content."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))


def main():
    tmp = tempfile.mkdtemp(prefix='v1_bug1_')
    os.makedirs(os.path.join(tmp, 'include'))
    os.makedirs(os.path.join(tmp, 'work'))
    with open(os.path.join(tmp, 'u.cpp'), 'w') as f:
        f.write('struct S { int a; float b; };\nstruct V { virtual void f(); int x; };\n')
    res = []
    for cxx in (None, os.path.join(HERE, 'cxx_msvc_semantics.sh')):
        env = dict(os.environ)
        if cxx:
            env['CXX'] = cxx
        else:
            env.pop('CXX', None)
        code = ('import layout, vtables; print(layout.probe_unit(%r, %r, "u.cpp", ["S"])); '
                'r = vtables.probe_host(%r, %r, "u.cpp", [(0, "::V")]); '
                'print({k: (v.get("enc"), v.get("vt_ents") is not None) for k, v in r.items()})'
                % (tmp, os.path.join(tmp, 'work'), tmp, os.path.join(tmp, 'work')))
        p = subprocess.run([sys.executable, '-c', code], cwd=os.path.join(HERE, '..', '..', 'link'), env=env,
                           capture_output=True, text=True)
        out = p.stdout.strip() + p.stderr.strip()[-400:]
        print('CXX=%s:\n  %s' % (cxx or '(tool default)', out.replace('\n', '\n  ')))
        res.append(out)
    ok = res[0].startswith("{'S': None}") and 'Layout(' in res[1]
    print('BUG-1 reproduced' if ok else 'BUG-1 not reproduced')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
