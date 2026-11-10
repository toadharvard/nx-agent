# L1 CONTRACT 005: state at the stop order (2026-11-10)

All work is committed and merged; nothing is half-written. Clean branch `claude/fnv-nx-clean` holds my head
f38298c (merge a15f312f, L1 verdict green, 69 s, pure 22/22, selftests 22/22).

## State in numbers
- N1 certificate: 68,354 of 68,657 decomp functions, 0 symbol conflicts.
- R6 vftable map: 4,118 (unit, `??_7`) pairs -> 2,325 addresses.
- R5 prototypes: 67,081 of 68,354.
- BODYSEL: total over 96,809 entries, all LIFTED so far (N3 no verdict 66,698; not decomp 28,152; N2 1,656;
  N1 303). NATIVE stays 0 until L2's objects with `.fnvnx.n3` arrive.
- L2 sections: reader (`contract/l2sections.py`) and binding through N1 (`contract/bind.py`, `--n3-out n3.tsv`)
  are done and tested on synthetic rows only. Not yet run on real L2 objects.
- d3dx reachability on L3's closed universes (fnv-nx-build `l1-contract/d3dx9_38` d11f7f1, `l1-contract/d3dx9_31`
  329045c): 29,689 of 37,980 and 10,597 of 18,580 entries; reachable imports 166 and 133 (gdi32: 15 in both).

## Waiting on others
- L4/L2: object shards `c/39b22cdeaec8/<k>of4` in fnv-nx-build.
- L3: universes republished under R3 (every relocated word whose value lies in an executable section is an
  entry). 17 such addresses were missing in d3dx9_38; L3 estimates about 1,449 more entries for the exe, so the
  exe `universe.tsv` md5 will change.

## Exact next steps on resume
1. Fetch one L2 shard; run `objfacts_one.py` per object, `objfacts_merge.py`, then
   `bind.py --obj obj.json --n1 n1.json --units units.json --out bind.json --n3-out n3.tsv`, then
   `bodysel.py --universe universe.tsv --n1 n1.json --gates <dir> --n3 n3.tsv --out bodysel.tsv`.
   Report the first NATIVE/LIFTED split.
2. When L3 sends new md5s: rerun BODYSEL and `link/tables.py` on the exe universe, rerun
   `dllreach_l3ranges.py` on both d3dx modules. The "owners of the byte" rule in that script then becomes
   unnecessary (the address is an entry itself).
3. Still open: `verdict.py` to the two-stage objfacts; N2 as equality of inlining closures; `link/resolve`;
   connect gates n1/bodysel/pcrel to `verify/gates.json`; loader S1, S5, S6 and removal of codeptr.c, trap.c,
   deldtor.c.

No farm job handed to L4: every ready L1 script needs the L2 shards as input and runs in about two minutes.
