#!/usr/bin/env python3
"""call_cycles.py - is there a call cycle through the memory-copy entry points of a linked guest? (S4: CopyMemoryBlock
0x00401460 forwards to memmove; once a recon body replaces the forwarder, no path may lead from libc's memcpy/memmove
back into it, or the guest recurses until its stack runs out.)

    python3 -I tools/link/call_cycles.py <guest.elf> [--roots NAME,...] [--depth N] [--json out.json]

Disassembles the ELF (objdump -d), takes the direct calls and tail branches of every function (bl/b to a symbol
outside the function, the PLT-less static link's only kind of call), and walks the graph from each root up to
--depth calls. A root reached again from itself is a cycle; each cycle is printed as names only. Roots (default): the
names the guest's units use for memory copies and the image functions bound to them: memcpy, memmove, _memcpy,
__memcpy, xmemcpy, FUN_00401460, __fnv_00401460, __fnv_00ec44d0, __fnv_00ec7230; names the ELF lacks are skipped.
Indirect calls (blr) are not followed: they are listed per root as `indirect` (a vtable or IAT slot could close a
cycle that this check cannot see).
Exit 0 without a cycle, 1 with one, 2 when the ELF or objdump is missing.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys

ROOTS = ['memcpy', 'memmove', '_memcpy', '__memcpy', 'xmemcpy', 'FUN_00401460', '__fnv_00401460', '__fnv_00ec44d0',
         '__fnv_00ec7230']
FUNC = re.compile(r'^([0-9a-f]+) <([^>]+)>:$')
CALL = re.compile(r'^\s*([0-9a-f]+):\s+(?:[0-9a-f]{8}\s+)?(bl|b)\s+([0-9a-f]+)\s*(?:<([^>+]+)(\+0x[0-9a-f]+)?>)?')
IND = re.compile(r'^\s*[0-9a-f]+:\s+(?:[0-9a-f]{8}\s+)?blr\s')


def objdump():
    return next((t for t in ('aarch64-none-elf-objdump', 'aarch64-linux-gnu-objdump', 'llvm-objdump')
                 if shutil.which(t)), None)


def graph(elf):
    """{function: (set of callees, indirect calls)}; aliases: {address: [names]} (one body, several symbols)"""
    tool = objdump()
    if not tool:
        return None, None
    out = subprocess.run([tool, '-d', '--no-show-raw-insn', elf], capture_output=True, text=True, errors='replace')
    g, cur, addr_names = {}, None, {}
    for line in out.stdout.splitlines():
        m = FUNC.match(line)
        if m:
            cur = m.group(2)
            addr_names.setdefault(int(m.group(1), 16), []).append(cur)
            g.setdefault(cur, [set(), 0])
            continue
        if cur is None:
            continue
        m = CALL.match(line)
        if m and m.group(4) and not m.group(5):   # to the start of a symbol: a call or a tail call
            if m.group(4) != cur:
                g[cur][0].add(m.group(4))
        elif m and m.group(4) and m.group(5) and m.group(4) != cur and m.group(2) == 'bl':
            g[cur][0].add(m.group(4))
        elif IND.match(line):
            g[cur][1] += 1
    # nm: the alias names objdump labels with one of them (e.g. __fnv_00401460 = FUN_00401460)
    nm = tool.replace('objdump', 'nm')
    if shutil.which(nm):
        by = {}
        for l in subprocess.run([nm, '--defined-only', elf], capture_output=True, text=True).stdout.splitlines():
            f = l.split()
            if len(f) >= 3 and f[1] in 'TtWw':
                by.setdefault(int(f[0], 16), set()).add(f[2])
        for a, names in by.items():
            addr_names.setdefault(a, [])
            for n in names:
                if n not in addr_names[a]:
                    addr_names[a].append(n)
    return g, addr_names


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('elf')
    ap.add_argument('--roots', default=','.join(ROOTS))
    ap.add_argument('--depth', type=int, default=6)
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    try:
        g, addr_names = graph(a.elf)
    except OSError:
        g = None
    if g is None:
        print('call_cycles: no objdump or no ELF', file=sys.stderr)
        return 2
    canon = {}
    for names in addr_names.values():   # every alias of one body -> the name objdump uses for it
        lead = next((n for n in names if n in g), None)
        for n in names:
            canon[n] = lead or n

    def callees(f):
        return {canon.get(c, c) for c in g.get(f, [set(), 0])[0]}

    res = {'schema': 'fnvnx-callcycles/1', 'roots': [], 'cycles': []}
    for r in a.roots.split(','):
        c = canon.get(r, r)
        if c not in g:
            continue
        seen, path, found = set(), [], []

        def walk(f, d):
            for x in sorted(callees(f)):
                if x == c:
                    found.append([r] + path + [x] if path else [r, x])
                elif x not in seen and d < a.depth:
                    seen.add(x)
                    path.append(x)
                    walk(x, d + 1)
                    path.pop()
        walk(c, 1)
        res['roots'].append({'root': r, 'function': c, 'reached': len(seen), 'indirect': g[c][1],
                             'callees': sorted(callees(c))})
        for cy in found:
            if cy not in res['cycles']:
                res['cycles'].append(cy)
    for rr in res['roots']:
        print('root %s (%s): calls %s; %d reached within %d; indirect %d' % (
            rr['root'], rr['function'], ', '.join(rr['callees']) or '-', rr['reached'], a.depth, rr['indirect']))
    for cy in res['cycles']:
        print('CYCLE ' + ' -> '.join(cy))
    print('call_cycles: %d roots, %d cycles' % (len(res['roots']), len(res['cycles'])))
    if a.json:
        json.dump(res, open(a.json, 'w'), indent=1)
    return 1 if res['cycles'] else 0


if __name__ == '__main__':
    sys.exit(main())
