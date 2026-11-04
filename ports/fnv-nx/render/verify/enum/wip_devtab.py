# V6n-render WIP (exploration): find developer shader tables written as `arr[i].f00 = "<x>.hlsl"` (ShadowLightShader
# VS table @0x00b71bf0, SpeedTreeFrond) and summarise them per function. Only covers that one table form; the other
# forms (brace init in lighting30shader, .source/.path/.pFile fields, shaderN[0], ShaderDesc("...")) are still to do.
#   python3 -I wip_devtab.py <fnv_decomp>/src
import re, sys, os, collections
root = sys.argv[1]
pat = re.compile(r'(\w+)\[(\d+)\]\.f00\s*=\s*"([^"]*\.hlsl)"')
fnre = re.compile(r'^// @fnv (0x[0-9a-fA-F]+)')
for dp, dn, fn in os.walk(root):
    for f in fn:
        if not f.endswith('.cpp'):
            continue
        p = os.path.join(dp, f)
        L = open(p, errors='replace').read().split('\n')
        cur = None
        tabs = collections.OrderedDict()
        for i, l in enumerate(L):
            m = fnre.match(l)
            if m:
                cur = m.group(1)
            for m in pat.finditer(l):
                k = (cur, m.group(1))
                tabs.setdefault(k, []).append((int(m.group(2)), m.group(3)))
        for (a, arr), ents in tabs.items():
            srcs = collections.Counter(s.replace('\\\\', '/').split('/')[-1] for _, s in ents)
            print("%s %s %s n=%d max=%d %s" % (os.path.relpath(p, root), a, arr, len(ents),
                                              max(i for i, _ in ents), dict(srcs.most_common(4))))
