"""r2_vorder.py - prepare rule VORDER1 of the recon fleet (R2): same-name virtual overloads in exe slot order.

MSVC lays out the overloads of one virtual function name together and in REVERSE declaration order. The decomp's
generated headers declare each such group in source order and record the exe slot of every declaration
(`virtual void Revert(unsigned int, MobileObject*);  // slot 0x15B` above `virtual void Revert(BGSLoadFormBuffer*);
// slot 0x15A`). GCC assigns slots in declaration order, so each group lands swapped in the native table. A caller
that reaches the object through a table in exe order (D26 image vtables) then runs the other overload (S2 seamcheck
VCALL, MobileObject::Revert @00932f96: exe slot 346, native 348).

VORDER1 sorts every run of adjacent same-name virtual declarations that carry `// slot 0xNN` comments into ascending
slot order. The lines are moved verbatim. The prepared tree is GCC-only; the MSVC build reads the decomp text.

VORDER1-LEFT (POST): after the rule, any class whose same-name virtuals still have slots that go down in declaration
order, adjacent or not. That case cannot be sorted by moving lines. The POST hook counts them and fails prepare,
unless FNVNX_VORDER_LAX=1 is set. The command line `r2_vorder.py <tree>` prints them and exits 1 if there are any.
"""
import os
import re
import sys

VDECL = re.compile(r'^\s*virtual\b[^;{]*?\b(~?\w+)\s*\([^;]*\)[^;]*;.*//\s*slot\s+(0x[0-9A-Fa-f]+)\b')


def _decl(line):
    m = VDECL.match(line)
    return (m.group(1), int(m.group(2), 16)) if m else None


def r_virtual_overload_order(path, text, stats):
    """VORDER1: sort runs of adjacent same-name virtual declarations into ascending `// slot` order."""
    if '// slot' not in text or 'virtual' not in text:
        return text
    lines = text.splitlines(keepends=True)
    i, changed = 0, 0
    while i < len(lines):
        d = _decl(lines[i])
        if not d:
            i += 1; continue
        j = i + 1
        while j < len(lines):
            e = _decl(lines[j])
            if not e or e[0] != d[0]:
                break
            j += 1
        if j - i > 1:
            run = lines[i:j]
            keyed = sorted(run, key=lambda l: _decl(l)[1])
            if keyed != run:
                # keep each line's own end-of-line (the last line of a file may have none)
                ends = [l[len(l.rstrip('\r\n')):] for l in run]
                lines[i:j] = [k.rstrip('\r\n') + e for k, e in zip(keyed, ends)]
                changed += 1
        i = j
    if not changed:
        return text
    stats['VORDER1'] += changed
    return ''.join(lines)


def descents(text):
    """[(line, name, previous slot, slot)]: same-name virtuals of one class body whose slot goes down in declaration
    order (the class body is approximated by brace depth: declarations at the same depth between two closing braces
    at a lower depth)."""
    out, seen, depth, stack = [], {}, 0, []
    for n, line in enumerate(text.splitlines(), 1):
        d = _decl(line)
        if d:
            key = (tuple(stack), d[0])
            if key in seen and d[1] < seen[key]:
                out.append((n, d[0], seen[key], d[1]))
            seen[key] = max(d[1], seen.get(key, -1))
        code = line.split('//', 1)[0]
        for ch in code:
            if ch == '{':
                depth += 1; stack.append(n)
            elif ch == '}' and depth:
                depth -= 1; stack.pop()
    return out


def scan_tree(root):
    found = []
    for top in ('include', 'src'):
        for dirpath, _, names in os.walk(os.path.join(root, top)):
            for name in names:
                if not name.endswith(('.h', '.hpp', '.inl', '.cpp')):
                    continue
                p = os.path.join(dirpath, name)
                with open(p, encoding='utf-8', errors='surrogateescape') as f:
                    t = f.read()
                if '// slot' not in t:
                    continue
                for row in descents(t):
                    found.append((os.path.relpath(p, root),) + row)
    return found


def post_check(out_root, stats, files):
    """VORDER1-LEFT: fail prepare when same-name virtuals are still out of slot order (FNVNX_VORDER_LAX=1: count only)."""
    left = scan_tree(out_root)
    stats['VORDER1-LEFT'] += len(left)
    if left:
        for r in left[:20]:
            sys.stderr.write('VORDER1-LEFT %s:%d %s slot %#x after %#x\n' % (r[0], r[1], r[2], r[4], r[3]))
        if os.environ.get('FNVNX_VORDER_LAX') != '1':
            raise SystemExit('VORDER1-LEFT: %d same-name virtual(s) out of exe slot order (tools/rules/r2_vorder.py)'
                             % len(left))


RULES = [r_virtual_overload_order]
POST = [post_check]

if __name__ == '__main__':
    rows = scan_tree(sys.argv[1])
    for r in rows:
        print('%s:%d\t%s\t%#x -> %#x' % r)
    sys.exit(1 if rows else 0)
