#!/usr/bin/env python3
# V6n (D14 verifier): reconstruct the TRUE FNV render-pass numbering.
#
# The decomp's enum header (src/bsshader/bsshader_shader_layout.h, eBSSMPassEnum) is not what the FNV binary uses.
# The binary carries its own pass-name function, FUN_00b4f9d0 (bsshadermanager.cpp, "BSSM_<name>" per case, compiled
# from the real enum), so every case label there is an id the shipping code knows by name. This tool:
#   1. parses the name function (true id -> name) and the header (header id -> name),
#   2. joins them by name and prints offset runs (header range -> true range, constant offset per run),
#   3. lists names present on only one side,
#   4. writes the joined table to <out>/pass_names.tsv (decomp-derived: <out> must be outside git, e.g. build/).
#
#   python3 -I render/verify/enum/pass_names.py <fnv_decomp> <out dir>
import os, re, sys, collections

def header_enum(path):
    out = collections.OrderedDict()
    marker = re.compile(r'^(RENDERPASSES_FIRST|RENDERPASSES_LAST|\w+_FIRST|\w+_LAST|\w+_START|\w+_END|COUNT|'
                        r'FIRST_ALPHA_PASS|POST_ALPHA_LAST|UNUSEDPASSES_FIRST|UNUSEDPASSES_LAST)$')
    markers = {}
    for l in open(path, errors='replace'):
        m = re.match(r'\s*BSShaderManager__eBSSMPassEnum_BSSM_(\w+)\s*=\s*(\d+)', l)
        if not m:
            continue
        n, v = m.group(1), int(m.group(2))
        if marker.match(n) and not n.endswith('RENDERNORMALS'):
            markers[n] = v
            continue
        out.setdefault(n, v)
    return out, markers

def name_fn(path, addr='0x00b4f9d0'):
    L = open(path, errors='replace').read()
    i = L.find('// @fnv %s' % addr)
    if i < 0:
        sys.exit('pass-name function @%s not found' % addr)
    j = L.find('\n}\n', i)
    body = L[i:j]
    pairs = [(int(v), n) for v, n in re.findall(r'case\s+(\d+)\s*:\s*return\s+"BSSM_(\w+)"\s*;', body)]
    return pairs

def main():
    dec, out = sys.argv[1], sys.argv[2]
    src = os.path.join(dec, 'src') if os.path.isdir(os.path.join(dec, 'src')) else dec
    hdr, markers = header_enum(os.path.join(src, 'bsshader/bsshader_shader_layout.h'))
    pairs = name_fn(os.path.join(src, 'bsshader/bsshadermanager.cpp'))
    true = {}
    for v, n in pairs:
        true.setdefault(n, v)
    ids = sorted(set(v for v, _ in pairs))
    print('header: %d named values (+%d range markers, COUNT=%s)' % (len(hdr), len(markers), markers.get('COUNT')))
    print('name function: %d cases, %d distinct ids, range %d..%d' % (len(pairs), len(ids), ids[0], ids[-1]))
    both = [n for n in hdr if n in true]
    only_h = [n for n in hdr if n not in true and n != 'DISABLED']
    only_t = [n for n in true if n not in hdr]
    print('joined by name: %d; header-only: %d; name-fn-only: %d' % (len(both), len(only_h), len(only_t)))
    runs = []
    for n in sorted(both, key=lambda n: (hdr[n], n)):
        d = true[n] - hdr[n]
        if runs and runs[-1][0] == d and hdr[n] - runs[-1][3] <= 8:
            runs[-1][3] = hdr[n]; runs[-1][4] = true[n]; runs[-1][5] += 1
        else:
            runs.append([d, hdr[n], true[n], hdr[n], true[n], 1])
    print('offset runs (header lo..hi -> true lo..hi, n):')
    for d, hl, tl, hh, th, c in runs:
        print('  %+4d  %3d..%3d -> %3d..%3d  n=%d' % (d, hl, hh, tl, th, c))
    holes = [i for i in range(1, ids[-1] + 1) if i not in set(ids)]
    print('true-id holes in 1..%d: %d' % (ids[-1], len(holes)))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'pass_names.tsv'), 'w') as f:
        f.write('true_id\tname\theader_id\n')
        for v, n in sorted(pairs):
            f.write('%d\t%s\t%s\n' % (v, n, hdr.get(n, '-')))
        for n in only_h:
            f.write('-\t%s\t%d\n' % (n, hdr[n]))
    with open(os.path.join(out, 'pass_names_holes.txt'), 'w') as f:
        f.write(' '.join(map(str, holes)) + '\n')
    print('wrote %s/pass_names.tsv' % out)

if __name__ == '__main__':
    main()
