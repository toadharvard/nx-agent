#!/usr/bin/env python3
"""prepare.py - derive the port's source tree from a decompilation checkout (stage tool, a pure function of its inputs).

    prepare.py <decomp root> <out dir> --win32-header FILE --crt-symbols FILE

Inputs (everything the output depends on, nothing else is read; no environment, no clock):
  <decomp root>     the checkout: src/ include/ build/ third_party/ (build/units.json is the recipe the rules read)
  --win32-header    the target's Win32 shim header (text): the names and prototypes DECL1 / CALL1 / SDK1 rest on
  --crt-symbols     the target's CRT symbol table (tab separated text): `crt_inline` rows name the out-of-line float overloads

Output: <out dir> holds src/ and include/ (every .cpp .h .hpp .inl .c file rewritten by the rules of README.md, the
other files copied), build/ and third_party/ (copied, then the POST hooks of the rules edit build/units.json) and
prepare_stats.tsv (rule id, files changed, edits). Files are written in sorted order; the same inputs give the same bytes.

Every rule is a function (path, text, stats) -> text, listed in the order they run: the core rules (core.py), then
XC1 (s10_startup), the compile rules (s1_compile), the body rules (s2_bodies) and the ASM rule (s8_asm). A rule counts
its edits in stats[<rule id>]. After the tree is written the dual-compile check (mscgate.py) runs over every
rewritten file and fails the run when a rule introduced a `_MSC_VER` conditional that is not of the permitted form.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core            # noqa: E402
import mscgate         # noqa: E402
import s10_startup     # noqa: E402
import s1_compile      # noqa: E402
import s2_bodies       # noqa: E402
import s8_asm          # noqa: E402

MODULES = [s10_startup, s1_compile, s2_bodies, s8_asm]
SOURCE_SUFFIXES = ('.cpp', '.h', '.hpp', '.inl', '.c')


def rule_list():
    """[(rule function)] in run order."""
    rules = list(core.RULES)
    for m in MODULES:
        rules += list(m.RULES)
    return rules


def post_hooks():
    return [h for m in MODULES for h in getattr(m, 'POST', [])]


def read_text(path):
    with open(path, encoding='utf-8', errors='surrogateescape') as f:
        return f.read()


def write_text(path, text):
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(text)


def copy_bytes(src, dst):
    with open(src, 'rb') as f:
        data = f.read()
    with open(dst, 'wb') as f:
        f.write(data)


def copy_tree(src, dst):
    """Content copy of a directory (sorted, no file times, no modes)."""
    for dp, _, names in core.walk_sorted(src):
        rel = os.path.relpath(dp, src)
        out = dst if rel == '.' else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for n in names:
            copy_bytes(os.path.join(dp, n), os.path.join(out, n))


def prepare(src_root, out_root, win32_header, crt_symbols):
    """Run the rules over the checkout. Returns (stats, files, texts, bad): the Counter of edits per rule id, of files
    changed per rule id, {relative path: (input text, output text)} of the source files, and the dual-compile
    violations found rule by rule."""
    src_root, out_root = os.path.abspath(src_root), os.path.abspath(out_root)
    core.configure(src_root, win32_header, crt_symbols)
    rules = rule_list()
    stats, files, texts, bad = Counter(), Counter(), {}, []
    for top in ('src', 'include'):
        for dirpath, _, names in core.walk_sorted(os.path.join(src_root, top)):
            rel = os.path.relpath(dirpath, src_root)
            os.makedirs(os.path.join(out_root, rel), exist_ok=True)
            for name in names:
                s, d = os.path.join(dirpath, name), os.path.join(out_root, rel, name)
                if not name.endswith(SOURCE_SUFFIXES):
                    copy_bytes(s, d)
                    continue
                text = orig = read_text(s)
                for rule in rules:
                    before = Counter(stats)
                    new = rule(s, text, stats)
                    bad += ['%s: %s' % (os.path.join(rel, name), v) for v in mscgate.check_rule_edit(rule.__name__, text, new)]
                    text = new
                    for k in sorted(stats):
                        if stats[k] != before[k]:
                            files[k] += 1
                write_text(d, text)
                texts[os.path.join(rel, name)] = (orig, text)
    if os.path.isdir(os.path.join(src_root, 'build')):
        copy_tree(os.path.join(src_root, 'build'), os.path.join(out_root, 'build'))
    # third_party/ (C units of units.json, compiled as C) is copied as it is: no rule edits it
    if os.path.isdir(os.path.join(src_root, 'third_party')):
        copy_tree(os.path.join(src_root, 'third_party'), os.path.join(out_root, 'third_party'))
    for hook in post_hooks():
        hook(out_root, stats, files)
    return stats, files, texts, bad


def main(argv):
    pos, opt, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in ('--win32-header', '--crt-symbols') and i + 1 < len(argv):
            opt[a], i = argv[i + 1], i + 2
        elif a.startswith('--'):
            sys.stderr.write('prepare.py: unknown option %s\n' % a)
            return 2
        else:
            pos.append(a)
            i += 1
    if len(pos) != 2 or len(opt) != 2:
        sys.stderr.write(__doc__.split('\n\n')[1] + '\n')
        return 2
    src_root, out_root = pos
    stats, files, texts, bad = prepare(src_root, out_root, read_text(opt['--win32-header']), read_text(opt['--crt-symbols']))
    bad += mscgate.check_tree(texts)
    with open(os.path.join(out_root, 'prepare_stats.tsv'), 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%d files\t%d edits\n' % (k, files[k], stats[k]))
    if bad:
        for line in bad:
            sys.stderr.write('prepare.py: dual-compile check: %s\n' % line)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
