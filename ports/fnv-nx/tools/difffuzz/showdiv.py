"""showdiv.py - print saved divergences (div/*.json) compactly: function, convention, divergence, minimal case."""
import glob
import json
import os
import sys

paths = []
for a in sys.argv[1:]:
    paths += sorted(glob.glob(os.path.join(a, '*.json'))) if os.path.isdir(a) else [a]
for p in paths:
    d = json.load(open(p))
    print('%s %s %s' % (os.path.basename(p), d['source'], d['symbol']))
    print('   conv:', d['conv'])
    print('   div :', json.dumps(d['divergence'])[:500])
    print('   hint:', json.dumps(d.get('hint')))
    c = d['case']
    print('   case: args=%s bg=%s rets=%d mem=%s' % (c['args'], c['bg'], len(c['rets']),
                                                    {k: v[:96] for k, v in c['mem'].items()}))
