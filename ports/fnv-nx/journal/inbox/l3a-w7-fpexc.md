# L3a w7: item 29, static FP-state facts (A-FPMASK / A-FPSTICKY / rounding at imports) for the 7-image set

Facts: fnv-nx-build `l3a-lift/facts` a0f3d8e (universe/ranges of all 7 images, tree_sha256 2a5bd36e…bb8dd). Tools: nx-agent `claude/fnv-l3a-lift-w7-merge`, `lift/tools/fpstate.py` (scan: A1 control-word loads, A2 readers, RC rows, impcalls.tsv),
`fpcensus.py` (per-site classes), `fpreach.py` (reachability). Outputs per image: `<facts>/fpstate/{fcw_loads,mxcsr_loads,readers,impcalls,scan,census,reach_imports,reach_imports_starts}.tsv` (private, in the farm copies; counts below).
Images: exe (FalloutNV.exe at 0x400000), d3dx9_38, d3dx9_31, binkw32, libvorbis, libvorbisfile, atimgpud (the w18 set + d3dx9_31). Every number is over ALL sites of the image.

## (a) FPMASK: does any load of the control word unmask an exception?
Per control-word load site (fldcw, ldmxcsr, fldenv, frstor, fxrstor, fninit, imports _controlfp...). Value forms: c = constant, t = transform new=(cur&A)|O, u = unknown (reason).
Proof (induction): the start word has the mask field all ones (FCW 0x027F bits 0-5, MXCSR 0x1F80 bits 7-12). A constant load has field V&M; a transform of a word whose field is M has field (A|O)&M. A site is
proven-masked iff that field is M; proven-unmasking iff it is not; unproven for u. I-FPMASK holds iff all sites are proven-masked.

| image | FCW loads: proven-masked / proven-unmasking / unproven | MXCSR loads: masked / unmasking / unproven | I-FPMASK |
|---|---|---|---|
| atimgpud | 0 / 0 / 0 | 0 / 0 / 0 | holds |
| binkw32 | 0 / 0 / 1 | 0 / 0 / 0 | unproven |
| libvorbis | 8 / 0 / 14 | 0 / 0 / 3 | unproven |
| libvorbisfile | 0 / 0 / 2 | 0 / 0 / 3 | unproven |
| d3dx9_38 | 84 / 0 / 47 | 0 / 0 / 0 | unproven |
| d3dx9_31 | 71 / 0 / 53 | 0 / 0 / 0 | unproven |
| exe | 771 / 0 / 50 | 1 / 0 / 3 | unproven |
| total | 934 / 0 / 167 | 1 / 0 / 9 | 1 of 7 holds |

No site in any image unmasks an exception (proven-unmasking = 0 everywhere). The unproven sites, by reason (a load whose word the scan cannot derive):
- binkw32: call 1.
- libvorbis: FCW arg 2, call 3, env-load (frstor) 1, global 4 (global 01d0c528), uninit 4; MXCSR call 1, espebp 1, uninit 1.
- libvorbisfile: FCW arg 1, call 1; MXCSR call 1, espebp 1, uninit 1.
- d3dx9_38: FCW alias 9, call 1, env-load (fldenv) 1, ptr 36. d3dx9_31: alias 6, call 1, ptr 46.
- exe: FCW alias 32, arg 2, call 3, env-load 2, global 6 (global 010f2a98), uninit 5; MXCSR call 1, espebp 1, uninit 1.
Read: `alias`/`ptr`/`global`/`arg`/`call` are loads of a word saved or passed from elsewhere (typically fnstcw / restore of the caller's word); `uninit` / `espebp` = the scan lost the stack slot. What would prove them is a value-set on the word's source
(the saved word is a member of the reachable set R, whose mask field is M by induction): a class-closing extension (memory-word flow), not a measurement. Until then the unproven sites are exactly the ones that need the full #MF/#XM semantics.

## (b) FPSTICKY: who reads the sticky exception bits?
Readers (A2): fnstsw whose value escapes (anything beyond C0-C3 can flow out: stored, returned, ANDed with a mask that keeps sticky bits...), fnstenv, fnsave, fxsave, stmxcsr, imports _statusfp / _status87 / _clearfp.
`cc-only` fnstsw (value consumed only through sahf / C0-C3 tests) observe no sticky bit. Clears (`listed`): fnclex.

| image | fnstsw cc-only | readers escaping (all kinds) | clears listed | I-FPSTICKY |
|---|---|---|---|---|
| atimgpud | 0 | 0 | 0 | holds |
| binkw32 | 2 | 0 | 1 | holds |
| libvorbis | 256 | 57 | 2 | unproven |
| libvorbisfile | 16 | 7 | 2 | unproven |
| d3dx9_38 | 4,187 | 314 | 0 | unproven |
| d3dx9_31 | 3,300 | 277 | 0 | unproven |
| exe | 9,444 | 2,454 | 2 | unproven |
| total | 17,205 | 3,109 | 7 | 2 of 7 hold |

Escape reasons (sites): exe: fnstsw ret/tail 1,886, call-reads-eax 334, use-mov 122, use-test 24, use-or 19, use-cmp 18, mem 10, use-shr 7, use-and 4, sahf-flagread-movsd 2, use-movzx 2, use-shl 1; stmxcsr image-to-memory 23, fnsave 1, fnstenv 1.
d3dx9_38: ret/tail 250, call-reads-eax 37, use-or 10, use-mov 5, use-cmp 3, use-test 3, use-sbb 2, mem 1, fnsave 3. d3dx9_31: ret/tail 206, call-reads-eax 35, use-or 17, use-test 9, mem 4, use-cmp 3, use-mov 3.
libvorbis: ret/tail 32, stmxcsr 10, mem 7, use-and 3, call-reads-eax 2, sahf 2, fnsave 1. libvorbisfile: stmxcsr 3, mem 2, ret/tail 1, use-or 1.
`ret/tail` and `call-reads-eax` are fnstsw values returned in ax to a caller (the function is a compare helper); whether the caller keeps only C0-C3 is what the reachability (d) and a per-caller mask rule would settle.

native_fpflags gate (w8's narrowing: no reader reachable from a native exit without a clear, while the reader's family owns the FPSR; rt/fnvl_x87.h fown, lines 32-51 at w8 37e3f069): `fpreach.py IMAGE FACTS --native FILE` implements the reachability half
(starts = post points of every direct call to a listed native address, all indirect post points if one is address-taken; stops at fnclex/fninit/_clearfp/_fpreset). It needs the native address list (bodysel's roots), which is not in the facts I have:
**blocked on the list from bodysel/w8**. The ownership half (family at the call site, f_x87_ctl.fown_prepass) is the lifter's own must-dataflow; the gate reads that fact per site. I have not run the gate.

## (c) Rounding control at every import call site
Image-wide reachable control words R (fixed point over all loads): RC field values; every import call site inherits the image's class (a context-insensitive over-approximation).
| image | import call sites (IAT calls + calls to import thunks) | x87 RC at them | SSE RC |
|---|---|---|---|
| atimgpud | 171 | proven nearest (R = {027F}) | proven nearest |
| binkw32 | 248 | unproven (1 unknown load) | proven nearest |
| libvorbis | 177 | unproven | unproven |
| libvorbisfile | 198 | unproven | unproven |
| d3dx9_38 | 618 | unproven: R has RC {0, c00 (truncate)} | proven nearest |
| d3dx9_31 | 433 | unproven: R has RC {0, c00} | proven nearest |
| exe | 6,660 | unproven: R has RC {0, c00} + 3 unknown MXCSR loads (FTZ 8000 reachable) | unproven |
| total | 8,505 | proven nearest: 1 image | proven nearest: 4 images |
The truncate word (RC=c00) comes from the 771 + 84 + 71 `fldcw` transforms (the `fistp` truncation idiom: set c00, fistp, restore). A per-site answer needs the set/restore pairing (path-sensitive); not done, so the sites are not proven nearest beyond the images marked.

## (d) Sticky reads reachable after an import returns, no clear in between
Reduced interprocedural reachability (fpreach.py): nodes = call posts, function entries, returns; edges = intra-function flow, direct calls, returns to every caller (through tail jumps), indirect calls to every address-taken entry (R3 set), import call = edge to its post only.
Stop at fnclex/fninit/_clearfp/_fpreset; hit = a reader of class `escapes`. Over-approximate (context-insensitive, indirect calls to every address-taken entry).
| image | import start points | starts that reach an escaping reader | distinct readers reachable |
|---|---|---|---|
| atimgpud | 171 | 0 | 0 |
| binkw32 | 246 | 0 | 0 |
| libvorbis | 177 | 174 | 46 |
| libvorbisfile | 247 | 244 | 7 |
| d3dx9_38 | 8,460 | 8,289 | 221 |
| d3dx9_31 | 4,520 | 4,514 | 189 |
| exe | 6,745 (6,660 IAT call sites + 85 calls to import thunks) | 6,676 | 1,889 |
| total | 20,566 | 19,897 | 2,352 (sum over images; of 3,109 escaping readers) |
Read: with no clear on the way, practically every import return can reach a reader in the three math-heavy images, so the sticky bits an import (parked FPSR) leaves cannot be dismissed statically by this over-approximation; the import boundary model (parking: imp.h fnvl_native_in/out, A-NATIVE) is what keeps them out, not the call graph. The images where I-FPSTICKY holds (atimgpud, binkw32) need nothing.

## What the facts leave open (needs your review before I write it)
1. I-FPMASK unproven in 6 images, 167 FCW + 9 MXCSR sites (list above). Proposal: do not write the full #MF/#XM trap semantics for them; add the memory-word value flow (a loaded word that is a saved copy has the mask field M by induction) and see how many sites remain; semantics only for the remainder.
2. I-FPSTICKY unproven in 5 images: 3,109 escaping readers. Full semantics = per-family shadow flag sets (already in fnvl_x87.h, w8). The residual question is only which readers keep sticky bits (the `ret/tail` returners).
3. native_fpflags needs the native address list.
