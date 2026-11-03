"""lockstep.py - run a whole call tree on both machines and compare the function-entry trace by original address.

    difffuzz.py lockstep <addr> [--source S] [--bg zero|rand] [--depth N] [--out DIR]

The tree: the root's unit plus every unit its oracle code reaches through direct calls (breadth first, up to
--depth units) that has both an MSVC object and a rewritten native object. x86 loads all their functions at the
original addresses; AArch64 links all their objects into one image. Calls leaving that set are intercepted on both
sides (canned results, as in the per-function mode); CRT builtins run. One deterministic run, world background
zero by default (an all-zero data window: what static initialisers see before the image is loaded).

Trace: every function entry by original address (x86: a block at a loaded function start; AArch64: a native entry
named by an `__fnv_` address symbol), plus intercepted calls. Alignment tolerates inlining: an entry one side never
makes anywhere in its trace is skipped (GCC or MSVC inlined that function); the first position where both sides
enter functions the other side also enters, but different ones, is the divergence. Final data writes are compared
too. Output: <out>/<addr>.json.
"""
import collections
import json
import os

from unicorn import UC_HOOK_BLOCK

import a64image
import arm64emu
import config
import conv as convmod
import engine
import runner
import world
import x86emu


def closure(uc, root_src, root_addr, depth):
    """Units of the functions reachable from the root through direct calls of the oracle code (breadth first over
    functions, at most `depth` units, only units that also have a rewritten native object)."""
    fixed = os.path.join(str(config.LINK), 'fixed')
    have = lambda s: os.path.exists(os.path.join(fixed, config.obj_name(s)))  # noqa: E731
    order, seen, todo = [root_src], {root_addr}, collections.deque([(root_src, root_addr)])
    while todo:
        src, addr = todo.popleft()
        try:
            f = uc.x86_unit(src, addr).funcs.get(addr)
        except Exception:  # noqa: BLE001
            continue
        if f is None:
            continue
        for _, kind, tg, _ in f['relocs']:
            if kind != 0x14 or tg is None or tg in seen or tg not in uc.def_at:
                continue
            s2 = uc.def_at[tg][0]
            if s2 not in order:
                if len(order) >= depth or not have(s2):
                    continue
                order.append(s2)
            seen.add(tg)
            todo.append((s2, tg))
    return order


def align(tx, ta):
    """First real divergence of two entry traces (inlining-tolerant), or None."""
    sx, sa = set(tx), set(ta)
    i = j = 0
    while i < len(tx) and j < len(ta):
        if tx[i] == ta[j]:
            i += 1; j += 1; continue
        if tx[i] not in sa:
            i += 1; continue
        if ta[j] not in sx:
            j += 1; continue
        return i, j
    return None


def run(a):
    uc = engine.UnitCache(str(config.LINK), str(config.A64WORK))
    root = int(a.addr, 16)
    src = a.source or uc.def_at[root][0]
    units = closure(uc, src, root, a.depth)
    code = {}
    for s in units:
        xu = uc.x86_unit(s, root if s == src else None)
        for ad, f in xu.funcs.items():
            if not f['unresolved']:
                code[ad] = f['code']
    pinned = {}
    for s in units:
        pinned.update(uc.pinned(s, root if s == src else next(iter(uc.x86_unit(s).funcs))))
    img = a64image.build_many(units, str(config.LINK), str(config.A64WORK), 'lockstep_%08x' % root)
    uc.preload(code, img, src)
    fn = uc.x86_unit(src, root).funcs[root]
    cv = convmod.Conv(fn['symbol'], src)
    case = world.Case(seed=1, bg=a.bg)
    w = world.World(case, code, pinned)
    t = runner.Target(cv, root, code, img, uc.stub_addrmap(img, src), uc.callee_info_for(src))
    t.vtables = uc.vtable_map(img)
    xe = x86emu.X86Engine(w, code, t.callee_info)
    ae = arm64emu.Arm64Engine(w, img, t.stub_orig, t.vtables)
    starts = set(code)
    tx, ta = [], []
    xe.uc.hook_add(UC_HOOK_BLOCK, lambda u, ad, sz, d: tx.append(ad) if ad in starts else None,
                   begin=world.TEXT[0], end=world.TEXT[1] - 1)
    entries = img.entries
    ae.uc.hook_add(UC_HOOK_BLOCK, lambda u, ad, sz, d: ta.append(entries[ad]) if ad in entries else None,
                   begin=a64image.A64_BASE, end=max(img.end, a64image.A64_BASE + 4) - 1)
    placement = t.placement()
    rx = xe.run(cv, case, placement, max_steps=a.max_steps, start=root)
    ra = ae.run(cv, case, placement, root, max_steps=a.max_steps)
    tx = [e for e in tx]
    first = align(tx, ta)
    div = runner.compare(cv, rx, ra, w, placement.get('sret'), t.local_statics, getattr(t.callee_info, 'conv', None))
    out = dict(root='%08x' % root, source=src, units=units, functions_x86=len(code), native_entries=len(entries),
               x86=dict(error=rx.error, entries=len(tx), calls=len(rx.calls)),
               arm=dict(error=ra.error, entries=len(ta), calls=len(ra.calls)),
               entry_divergence=None, result_divergence=div)
    if first:
        i, j = first
        out['entry_divergence'] = dict(x86_index=i, arm_index=j, x86=['%08x' % e for e in tx[max(0, i - 4):i + 3]],
                                       arm=['%08x' % e for e in ta[max(0, j - 4):j + 3]])
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, '%08x.json' % root), 'w'), indent=1)
    print(json.dumps(out, indent=1)[:3000])
