### J-XXXX | 2026-11-12 | stream: L3a-lift w4 | status: design | parent: J-XXXX (write-set census a3fcd7fc)

* **did** - tools/writeset.py (a3fcd7fc): the store census over the 7 images (facts 2eeea27b, gamedata/exe 0948da1e, redist c53729e).
  Globally 1,893,456 stores: exact 38,925, stack 1,227,422, open or ranged 627,109 (the exe 303,118); 8,240 import call sites write memory
  outside the census. Neither "no store reaches the exe name bytes 0x1273000 / 0x127300a" nor "the only writer of [0x1ab3b24] in d3dx9_38 is
  the ctor at 56b74b" closes by the operand form: the ctor writes through eax (this). A closing rule needs the value of the base register.
* **design: whole-program points-to over abstract objects (not coded; for review before coding)**
  - **Objects.** Finite set O of (a) static objects: one per image section, with a field per 4-byte word and one per relocated data word
    (R3 gives each word's class: a code reloc or a data reloc, so a `.data` word holding a static-object address is a points-to fact of the
    image, not a guess); (b) stack frames: one object per entry, fields = the esp-relative slots pcsrc already tracks (its ('sp',k) values),
    plus its incoming argument slots; (c) heap objects: one per allocation SITE, where a site is a call whose target is an allocator entry
    given by L1 w18's allocation-site fact file (no second list); a field per constant offset; (d) TOP = unknown.
  - **Values.** A register or a cell holds a set of (object, offset | ANY) pairs, or a small integer constant set, or TOP. Offset arithmetic
    by a constant stays exact; arithmetic by a non-constant collapses that object's offset to ANY (monotone, field-insensitive for that
    object only). pcsrc's typed code values (ra, fip, s1, s4) are separate tags of the same value, so one dataflow serves both rules.
  - **Memory.** A map cell -> value set; flow-insensitive per cell (Andersen style, inclusion constraints), flow-sensitive inside one entry
    for registers and stack slots (the pcsrc state). A store `[v]=w` adds w to every cell in gamma(v); a load reads the union over gamma(v).
  - **Interprocedural.** An entry's incoming registers and argument slots are the join over its call-site states (pcsrc `feed` already
    does this for the code tags). An indirect call resolves through the points-to set of the call operand's code values and iterates
    with the dataflow until stable; an indirect call with a TOP operand, an export, an entry or an unknown caller gets TOP arguments.
  - **Soundness of open stores.** Every instruction's transfer over-approximates the concrete semantics (a pure function of the pcsrc state
    and the decoded instruction). So the concrete write set of the image is contained in the union over stores of gamma(address value).
    A store whose address value is TOP is an OPEN store: it may write any cell, so every query is answered OPEN while the open count is
    greater than 0. The number of open stores is the bound that the census already gives (627,109 by form); the points-to lowers it by
    every store whose base register has a non-TOP value, and the final count is reported globally per image with the commit. Writes by
    imports use the import manifest's write contract (L5): an import with no contract writes TOP through every pointer-valued argument, so it
    is counted as open. Nothing is taken as closed: a query is PROVEN only with zero open stores or with the open stores' value sets
    disjoint from the range, and OPEN otherwise.
  - **Termination.** The value lattice has finite height: O is finite (sections, relocated words, entries' slots, allocation sites),
    offsets are bounded by the object size or ANY, constants are a bounded set (collapsing to TOP above a fixed set size, a lattice
    widening, not a step cap), so every cell and register value can only grow a bounded number of times. The worklist algorithm
    therefore reaches the least fixpoint; there is no iteration limit in the code.
  - **Query.** `reach(lo, hi)` returns the exact writers (stores whose gamma meets the range and that are not TOP) and the open count.
    The two stated queries become: (3) every store in 7 images with gamma meeting [0x1273000, +len) is listed, none by TOP; (1) the ctor
    store at 56b755/56b758 has gamma = { (static 793b20, +4) } from the callers' abs 793b20 and the static initializer word, and it is the
    only store with that cell in its gamma.
* **premises** - none new (ASM-225 is the only open one used). It rests on ASM-225 (fresh stack space) for the entries that create one; those entries' results are labelled
  cond:fresh and a store inside a fresh space is not counted as a write to the old frame.
* **next** - the four pcsrc counts for the 7 images at 9d71e6cd, run_facts to w7; then this design coded in tools/pointsto.py after the
  lead's review, run on the facts the moment w7 lands the next commit.
