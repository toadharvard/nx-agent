#!/usr/bin/env python3
"""linfeed.py - an S18 scenario plan (fnvnx-scenario-plan/1, tools/scenario/scenario.py compile) as a linhost feed
(tests/s15/mtqemu/linhost.c --feed), so the scenario's keys reach the guest on real host threads under qemu as they
reach it in the emulator (tools/scenario/emu_scenario.py): same steps, same keys (tools/scenario/keys.py), same
press timing (downs at once, ups half a gap later, the next step after the gap).

    python3 -I tools/perf/linfeed.py <plan.json> --elf <guest.elf> [--milestones tests/s5/milestones.txt] --out FILE

Milestones (the plan's `until milestone`): the milestone table's `call` lines become hooks - `call 0xORIG` the
image function's native symbol __fnv_<orig>, `call re:REGEX` every ELF function symbol it matches - and linhost
marks a milestone reached when one of its functions is entered. `data` and `store` milestones are not visible from
linhost: such a step waits its timeout and is logged "until unsupported". A key the vocabulary does not know is
skipped and listed on stderr.

Feed format (fnvnx-linfeed/1, one step per line, times in ns):
    hook 0x<addr> <milestone>      press <gap> <usage>:<down> ...      wait <ns>
    until-hook <timeout> <milestone>      until-file <timeout> <path>      until-none <timeout> <milestone>
    mark <name>
Exit 0; 2 for an unreadable plan.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(P, 'tools', 'scenario'))
import keys  # noqa: E402  (S18's key vocabulary)


def ns(s):
    return int(float(s) * 1_000_000_000)


def milestones(path):
    out = []
    for line in open(path):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        p = line.split(None, 2)
        if len(p) == 3:
            out.append((p[0], p[1], p[2].strip()))
    return out


def symbols(elf):
    nm = next((t for t in ('aarch64-none-elf-nm', 'aarch64-linux-gnu-nm', 'llvm-nm', 'nm') if shutil.which(t)), None)
    if not nm or not elf:
        return {}
    syms = {}
    for line in subprocess.run([nm, '--defined-only', elf], capture_output=True, text=True).stdout.splitlines():
        f = line.split()
        if len(f) >= 3 and f[1] in 'TtWw':
            syms.setdefault(f[2], int(f[0], 16))
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('plan')
    ap.add_argument('--elf')
    ap.add_argument('--milestones', default=os.path.join(P, 'tests', 's5', 'milestones.txt'))
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    try:
        plan = json.load(open(a.plan))
        steps = plan['steps']
    except (OSError, ValueError, KeyError) as e:
        print('linfeed: %s: %s' % (a.plan, e), file=sys.stderr)
        return 2
    syms = symbols(a.elf)
    hooks, unseen = [], set()
    for name, kind, target in milestones(a.milestones) if os.path.isfile(a.milestones) else []:
        if kind != 'call':
            unseen.add(name)
            continue
        if target.startswith('re:'):
            rx = re.compile(target[3:])
            addrs = sorted({v for k, v in syms.items() if rx.search(k)})
        elif re.fullmatch(r'0x[0-9a-fA-F]+', target):
            v = syms.get('__fnv_%08x' % int(target, 16))
            addrs = [v] if v is not None else []
        else:
            addrs = [syms[target]] if target in syms else []
        hooks += [(ad, name) for ad in addrs]
    hooked = {n for _, n in hooks}
    lines, skipped = ['fnvnx-linfeed/1'], []
    for ad, name in hooks:
        lines.append('hook 0x%x %s' % (ad, name))
    for s in steps:
        op = s.get('op')
        if op == 'press':
            try:
                evs = keys.press_events(s['key'])
            except KeyError:
                skipped.append(s['key'])
                continue
            lines.append('press %d %s' % (ns(s.get('gap', 0.25)), ' '.join('%d:%d' % (u, d) for u, d in evs)))
        elif op == 'wait':
            lines.append('wait %d' % ns(s.get('s', 0)))
        elif op == 'until':
            t = ns(s.get('timeout', 600))
            if 'file' in s:
                lines.append('until-file %d %s' % (t, s['file']))
            else:
                m = s.get('milestone', '?')
                lines.append('until-%s %d %s' % ('hook' if m in hooked else 'none', t, m))
        elif op == 'mark':
            lines.append('mark %s' % s.get('name', '?'))
    open(a.out, 'w').write('\n'.join(lines) + '\n')
    used = {s.get('milestone') for s in steps if s.get('op') == 'until' and 'milestone' in s}
    print('linfeed: %d steps, %d hooks (%s); milestones linhost cannot see: %s; keys skipped: %s' %
          (len(lines) - 1 - len(hooks), len(hooks), ', '.join(sorted(hooked)) or '-',
           ', '.join(sorted(m for m in used if m not in hooked)) or '-', ', '.join(skipped) or '-'), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
