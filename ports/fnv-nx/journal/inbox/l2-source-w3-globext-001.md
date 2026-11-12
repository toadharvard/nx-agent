# globext: the /Z7 tree does not match the pristine tree (w3, 2026-11-12)

Measured at branch claude/fnv-l2-source-w3-globext 9ddae252, with link/globext_cv.py `bind_unit` on the real objects
(pristine msvc/pristine/87eb9373a0bb, z7 msvc/z7/8f324ecde2a7 shards 1..4 of 4 at dd3e3088, 2c4f2f2d, 276fc311, 2b8ec9cd;
units.json of l1-contract/inputs/e1ca6b02d60d). N1 was not run; this is the per-object equality only.

## Bind totals (2,987 pristine objects, every one judged)
| result | units |
|---|---:|
| z7 object equals the pristine object | 699 |
| unequal | 2,250 |
| no z7 object (cl failed) | 38 |

## Breakdown by reason
| reason | units |
|---|---:|
| sections differ | 2,050 |
| symbols differ | 200 |
| no z7 object (cl failed or not built) | 38 |

(A 39th z7 unit failed in cl and has no pristine object either: 2,988 units in the z7 tree, 2,949 built, 2,939 `.obj` files in the four shards, 39 `.err`.)

## u0 (src/NiRenderedTexture.cpp), non-debug sections as `name size`
- z7:      .drectve 93, .text 376, .text$x 38, .xdata$x 44, .sxdata 4, .text 94, .text 6, .text 14, .text 14, .text 4
- pristine: .drectve 47, .text 304, .text 94, .text 6, .text 14, .text 14, .text 4

The z7 object has three sections the pristine one lacks (.text$x, .xdata$x, .sxdata: an exception-handler / safe-SEH table) and a longer first
.text (376 against 304) and .drectve (93 against 47). That is a different compilation (flags or compiler), not /Z7 alone.

## Not a rule
A diagnostic only, not proposed: with `.drectve` ignored, 1,168 of the 2,949 would be equal; of the 1,781 that still differ, the first differing section
is a different section count for 815, `.sxdata` for 632, `.text` for 317, a section name for 10, `.rdata` for 5.

## Fix, when the item resumes
At the source: rebuild the /Z7 tree with exactly the pristine flags plus /Z7, so the per-object proof holds. The proof (bind_unit) stays as it is.
