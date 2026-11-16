#!/usr/bin/env python3
"""Code universe of an image: the one engine (modlift.py). Roots by property (entry point, every relocation target in an executable section,
exports, eh/seh entries, tls); closure over instruction flow; byte accounting. The exe is a PE like any module: its row is built from the file.
Output: OUTDIR/<image>/{universe,codemap,ranges,...}.tsv and the gate JSONs; exit 1 if any byte is unexplained."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # the tool root: a tool imports its siblings from its own root (the generated stages run it under python3 -I, which has no script directory on the path)
from types import SimpleNamespace
import cli
USAGE = '''usage: universe.py --out OUTDIR (--exe FILE | --module NAME --modules modules.tsv --moddirs DIR[:DIR...]) --lib DIR --keys keys.tsv --baddecode FILE --shapes FILE --udsail FILE
  --baddecode / --shapes: the image's refused-sequence list and decoded-shape universe (BADDECODE, SHAPES outputs). --udsail: the Sail #UD verdicts (proof/udsail.py) for the refused sequences.
  code universe of the exe (--exe FILE) or of the module NAME (a name or file of modules.tsv; the module table and the directories that hold the module files are declared inputs).
  --keys: the import conventions (keys.tsv). --lib: the directory that holds the package fnvlift. Every path is an explicit argument: no environment variable, no default.'''
_OPTS = {'--out': True, '--exe': True, '--module': True, '--modules': True, '--moddirs': True, '--keys': True, '--lib': True, '--udsail': True, '--baddecode': True, '--shapes': True}
_POS, _O = cli.parse(USAGE, sys.argv[1:], (0, 0), _OPTS)
def _need(*ks):
    miss = [k for k in ks if k not in _O]
    if miss: sys.exit('universe.py: missing %s\n\n%s' % (' '.join(miss), USAGE))
_need('--out', '--keys', '--baddecode', '--shapes', '--udsail', '--lib')
if ('--exe' in _O) == ('--module' in _O): sys.exit('universe.py: exactly one of --exe, --module\n\n' + USAGE)
import ehflow; ehflow.configure_keys(_O['--keys']); sys.path.insert(0, _O['--lib'])      # --lib: a declared input of the stage
import modlift
if '--exe' in _O: modlift.run('exe', SimpleNamespace(exe=_O['--exe'], modules=None, moddirs=[], facts_out=_O['--out'], udsail=_O['--udsail'], baddecode=_O['--baddecode'], shapes=_O['--shapes']))
else:
    _need('--modules', '--moddirs'); modlift.run(_O['--module'], SimpleNamespace(modules=_O['--modules'], moddirs=_O['--moddirs'].split(':'), facts_out=_O['--out'], udsail=_O['--udsail'], baddecode=_O['--baddecode'], shapes=_O['--shapes']))      # modlift.run raises on failure; its verdict is in the gate JSONs
