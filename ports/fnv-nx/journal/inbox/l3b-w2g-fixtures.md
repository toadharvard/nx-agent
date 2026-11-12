# l3b-w2g: fixtures and rewritten tests, state at 6383b18f

## Fixture root (prepared, not admitted): lift/proof/fixtures (build.json in w16's format, msvcmodel external)
| unit | gate | expected code | waits on |
|---|---|---|---|
| fx_rcp.cc | fpexact | rcp-estimate | w16 + w10 in one base, admission of the root |
| fx_undec.cc | fpexact | undecodable | same |
| fx_nosite.cc | fpexact | fcvt-no-site | same, and the L2 plugin (fnvnx_ftoi, 649a2960) for the report |
| fx_conv.cc | fpexact | f2i-conv | same |
| fx_asm.cc | fpexact | f2i-asm | same |

## Not fixtures (plain cases on recipe-built output)
- f2i-report-missing: remove a real report from a recipe-built output (needs the plugin).
- builtin finding is green (needs the plugin).

## formgate cases waiting (a broken pack or root-level fixtures, L1; and a lifted root's real pack, L3a)
| case | expected code / item | waits on |
|---|---|---|
| shapes.tsv missing | `<root>:shapes` | root-level fixture form (L1) + lifted root |
| shapes.tsv malformed, unsorted or duplicate key | `<root>:shapes` | same |
| pack hash mismatch / pack does not fetch | `<root>:shapes` | same |
| shape with no symbolic Form | uncertified shape | lifted root pack |
| cert row with wrong dsl_sha or model_sha | uncertified shape | lifted root pack + w4 certs rows (19 494 shapes) |
| inputs: model_sha256 differs from sail_pins.txt, bad external, undeclared certs external | `inputs` | root-level fixtures or a real broken inputs file |

## Tests to rewrite or delete (doubles)
- tests/test_formgate.py: fake externals and shapes.tsv via fakeext -> real pack external of the lifted root (needs L3a root).
- tests/test_fpexact.py: direct `as` and hand-written f2i files -> recipe-built fixture objects and the plugin's reports.
- tests/fakeext.py: delete with its last user.
- tests/test_formgate_real.py: done, 0 doubles.
