# V6n verification status

V6n is the independent verifier (D14) of the native renderer. Method and results: `docs/RENDER_VERIFY.md`.

## Done
1. True pass numbering (`render/verify/enum/pass_names.py`): 759 ids, anchored on the binary's pass-name function
   (742 cases) + 6 code anchors. The header enum is wrong for FNV (RENDER_VERIFY.md §1).

2. Pass-space enumerator (`render/verify/enum/pass_space.py`): 758 declared, 742 named, 637 configured,
   439 emitted, 753 in an accumulator render group, **395 reachable** (per class: RENDER_VERIFY.md §2).

## In progress
- Nine oracle host tool.

## Rebuild
```sh
python3 -I render/verify/enum/pass_names.py ext/fnv_decomp build/v6n     # -> build/v6n/pass_names.tsv
python3 -I render/verify/enum/pass_space.py ext/fnv_decomp build/v6n     # -> build/v6n/pass_space.tsv (+ counts)
```

## S6n claims checked
| claim (source) | verdict |
|---|---|
| RENDER_NATIVE.md §1.3: pass enum 756 values, 667 live, FIRST_ALPHA_PASS=494, family ranges | **FAIL**: 759 ids, FIRST_ALPHA_PASS=568, family ranges shifted +2..+86 |

## Defects filed (tests/v6n/DEFECTS.md)
none yet
