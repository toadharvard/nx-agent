### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-004

* **d25-sg** (claude/fnv-s1-rules 6b31066, harness) - the oracle's $SG literals are mapped through the unit's
  numbering shift (coffobj.sg_shift, the delta chosen per unit by the exe's bytes). EQ's report: units.json $SG
  overlaps, 3,334 pinned literals differing from the exe.
  - EQ's count over all 2,988 units with the owner's exe: shift 0 in 2,684 units, +43 in 299, +1 in 4. This
    corrects the commit body: it is not every C++ unit, and the per-unit choice is what makes it right.
  - Pinned literals differing from the exe: 3,278 -> 3,179. Overlaps: 37 -> 0.
  - Of the 3,179: 10 string vs string (equal over the first 30 bytes), 91 binary bytes where the exe has a string
    (possibly a second mapping class; list requested from EQ), and 3,078 binary vs binary (constant tables and
    unrelocated words, not $SG; a separate question).
  - d25-sg refuzzes only the functions whose oracle closure refers to a $SG address. The next fuzz pass measures the
    hard count.
* **D26** (wip/d26 = 686c576): waiting for try1of1's full build + stub boot and the fuzz pass. The compile fixes are
  verified by a full census, 2,978 / 2,978 clean (s1-rules-005 on wip/d26).
* **d25-sg, part 2** (f882f1d): pinned read-only tables now carry their DIR32 relocations. EQ's 91 "bin-at-str"
  rows were vtables and pointer tables pinned as zeros. EQ's recount over all 2,988 units with the owner's exe:
  **pinned literals differing from the exe 3,278 (head) -> 3,179 (shift) -> 72 (relocations), in 57 units;
  overlaps 0, bin-at-str 0.** Left: 62 binary vs binary and 10 string literals over exe binary bytes (list
  requested).
