"""slotfill2.py - S2c-vt2's additions to the exe slot fill (fnvlink.exe_slot_fill; docs/LINK.md "Vftables from the
player's exe"). fnvlink.py keeps a few hook lines; the rules live here so S2c-vt's and S2c-vt2's edits stay apart.

Rules:
* **Secondary-base entries** (`secondary_entry`): the exe slot of a secondary vftable (COL offset d > 0) holds an
  override that MSVC compiled with `this` at that base subobject (thisadj.py; the address map names its entry
  `__fnvnx_msvc_<a>`, an alias of GCC's `_ZThn<d>_` thunk). The native slot of a secondary vtable passes the
  subobject too, so binding it to `__fnv_<a>` (the full-object entry) hands the definition a `this` that is d bytes
  too high. When every vtable naming the slot has offset-to-top -d, the slot binds to `__fnvnx_msvc_<a>` instead.
"""


def secondary_entry(target_for, native, addr_sym, entry_sym, stats=None):
    """Wrap exe_slot_fill's target_for(un, a, otts, depth): a direct binding `__fnv_<a>` of an address MSVC enters
    at a secondary base d becomes `__fnvnx_msvc_<a>` when the native vtables naming the slot sit at that base
    (every offset-to-top is -d). Other results are returned unchanged."""

    def wrapped(un, a, otts=None, depth=0):
        tgt, why = target_for(un, a, otts, depth)
        nd = native.get(a) if tgt == addr_sym % a else None
        me = nd.get('msvc_entry') if nd else None
        if me and otts and all(-o == me[1] for o in otts):
            if stats is not None:
                stats['secondary_entry'] += 1
            return entry_sym % a, '%s; msvc this = base subobject +%d: the _ZThn thunk' % (why, me[1])
        return tgt, why
    return wrapped
