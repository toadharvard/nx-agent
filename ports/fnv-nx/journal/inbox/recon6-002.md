### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **closed** - 54 of the 112 shard names, in 15 fnv-nx-build branches `recon/<unit>` (each: body.cpp, recon.json
  fnvnx-recon/1, match.json, README). All are (a): the unit with the bodies under MSVC 9 against the owner's exe
  matches every function and byte, and the prepared tree's unit with the bodies compiles natively (aarch64 ILP32).
  - 37 extern-text names: three exe functions no unit placed (25 + 8 + 4 names bound to them), now REC1
    `functions`: a strcat_s wrapper, an _mbsnicmp wrapper and a file-list forwarder (25, 25, 35 bytes).
  - 9 conversion / deref operators: bodies that compile to the shared /Od getter 0x00559450 (16 bytes). That address
    is already defined by a unit, so they are recon.json `aliases`, not `functions`: REC1-DUP would skip the whole
    recon. The native object then defines the name itself.
  - 8 bindings: the name calls a function a unit already defines and matches (an allocator, a refcount release, a
    pass-list add, a list RemoveAll ...). recon.json `symbols` binds the MSVC name; each binding parses to the same
    fnvlink `exact` key as the trap name (checked with tools/link/sigs.py).
  - a/b split: 54 a, 0 b.
* **class for the orchestrator (tools/link, not R6's to change)** - fnvlink resolve leaves 82 distinct native names
  `unresolved` although their unit binds the MSVC name: conversion operators to a pointer type (`cvP...`) and unary
  `operator*` (`deEv`).
  - Conversion operators: `sigs.parse_msvc` splits `X::operator struct N::T *` at the `::` inside the target type,
    so the last component does not start with `operator` and the sig becomes `special`.
  - Failing case: `??BSpherePtr@fnv_009614b0@@QBEPAUSphere@1@XZ` against
    `_ZNK12fnv_009614b09SpherePtrcvPNS_6SphereEEv` gives `Sig(special)`.
  - Patch (tested offline on 4 pairs, all now the same `exact` key) below. It closes the conversion half for every
    shard, so later recons need no alias body.
  - `operator*`: `Flt::operator*` parses to identical exact keys on both sides, but all 39 `...deEv` references are
    unresolved on link/9f865b0a4037. The cause is somewhere in resolve, not in sigs; not found yet.
* **blocker (S2-bodies / rules)** - `List62::NewItem` and `Item62::SetElement` (blk_00c60000_od_ob1_variant) have no
  exe counterpart. The decomp's inline `List62::Add62` is mis-reconstructed: the exe's 0x00c62cc0, to which its name
  is bound, is an array Add (grow via 0x00c628c0, then SetAt 0x00c62b90), not a list append. The fix is a tree rule
  that makes Add62 out of line, bound to 0x00c62cc0, which removes both references. Bodies would be wrong.
* **method note** - binding by call count alone (no bound call to anchor the order) mis-aligns when the decomp's
  caller is off: `ac8parse::Input::Input` lines up with the streambuf constructor in 0x00acc6c0. Such hits are
  checked by hand against the exe call context before they are used. 5 iostream names of tesobject_ob1_variant are
  still being checked.
* **metric** - shard names still without a body: 112 -> 58.

```diff
--- tools/link/sigs.py	2026-11-07 20:02:11.930708666 +0000
+++ tools/link/sigs.py	2026-11-07 20:12:36.877994383 +0000
@@ -92,6 +92,19 @@
     return [p.strip() for p in out if p.strip()]
 
 
+CONV_OP = re.compile(r'(?:^|::)\s*operator (?!new\b|delete\b)(?=[A-Za-z_])')
+
+
+def split_qual_conv(qual):
+    """split_qual for a conversion operator too: 'A::B::operator struct N::T *' -> ['A', 'B', 'operator N::T*'] (the
+    target type is one component, canonical, so MSVC and Itanium spell it the same)."""
+    m = CONV_OP.search(qual)
+    if not m:
+        return split_qual(qual)
+    at = qual.index('operator', m.start())
+    return split_qual(qual[:at].rstrip(':').strip()) + ['operator ' + canon_type(qual[at + len('operator '):])]
+
+
 def matching_open(s, close_at):
     depth = 0
     for i in range(close_at, -1, -1):
@@ -288,10 +301,10 @@
     pclose = matching_close(post, popen)
     qual = post[:popen].strip()
     # an operator ends in '*' too: operator*, operator*=, operator->*, a conversion operator 'operator T *'
-    op = (split_qual(qual) or [''])[-1].startswith('operator')
+    op = (split_qual_conv(qual) or [''])[-1].startswith('operator')
     if (qual.endswith('*') and not op) or qual.endswith(')'):  # function returning a function pointer: unsupported
         sig.kind = 'special'; return sig
-    sig.qual = canon_qual(split_qual(qual))
+    sig.qual = canon_qual(split_qual_conv(qual))
     sig.raw_params = _parse_params(post[popen + 1:pclose])
     sig.params = sig.raw_params
     tail = post[pclose + 1:]
@@ -402,7 +415,7 @@
     qual = head[cut:].strip() if cut else head.strip()
     if not qual or qual.startswith(('*', '&')):
         qual = head.strip(); cut = 0
-    sig.qual = canon_qual(split_qual(qual))
+    sig.qual = canon_qual(split_qual_conv(qual))
     sig.params = _parse_params(s[popen + 1:pclose])
     sig.raw_params = sig.params
     sig.cv = ' '.join(w for w in ('const', 'volatile') if re.search(r'\b%s\b' % w, tail.group(1)))
```
