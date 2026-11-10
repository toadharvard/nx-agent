# render/shaders/total - SM1-3 translator totality gate

Rule (owner): the shader translator is TOTAL over the D3D9 SM1-3 language (not over the game's shaders); anything untranslated is a build error.
`tests/l6/test_sm_total.py` is that gate. Run: `python3 -I tests/l6/test_sm_total.py --build /home/user/work/l6/sm [--classes]` (about 15 s).

## Pipeline
`sm_table.py` -> `sm_table.json` (the language definition; every entry has `source`) -> `sm_universe.py` (+`sm_universe2.py`, `sm_emit.py`) rows
-> `sm_run.py` (encode with `sm_hand.py`, ps_1_x CTAB rows via s6b_asm; translator `sdp2dksh --no-dksh`, then uam) -> `sm_total.tsv` + classes (`sm_classes.py`).
`sm_table.py` needs the reference caches (not committed): MS pages (`sm_docs.py`, fetched from learn.microsoft.com direct3dhlsl dx9-graphics-reference-asm-*),
wine and vkd3d clones (`sm_xref.py` READS their opcode/register tables; no code copied). The committed JSON is what the gate reads.

## Sources and how they are used
- Opcode model sets: the MS per-instruction version tables (parsed), cross-checked per (stage, opcode number) against vkd3d d3dbc.c (strict) and wine
  wined3d shader_sm1.c (a decoder: min/max 0 = unconstrained, so wine wider than MS is silence; wine lacking an MS model is a conflict).
- Registers/modifiers/swizzle/masks/dcl/flow/caps: MS register, modifier, flow-control, device-caps pages (transcribed in sm_rules*.py), wine asmparser.c register tables.
- `conflicts` (4): sources disagree (ps defb in ps_2_0, ps texreg2gb in ps_1_1, vs_3_0 v[a0.x], sgn operand count). The universe contains the union.
- `undetermined` (6) + legacy `unreachable-doc` (2 families): no source states the fact; the universe includes the superset. `unresolved` counts them.
- Not creatable on a hardware device: vs_2_sw/vs_3_sw/ps_2_sw/ps_3_sw (MS software-shaders: software VP / reference device only). vs_1_0/ps_1_0 are undocumented
  (included as 1_1 grammar, rows tagged undet). Constants: caps-dependent (`env_default`: 256 vs, fixed per ps model; 8192 is *_sw only).
- Instruction-slot, executed-instruction, read-port, dependent-read and texld-count limits are validator rules, not translator features (not in the universe).
- Caps-gated sm_2_x features (predication, dynamic/static flow depth, arbitrary swizzle, gradients, NumTemps) are listed in `caps` with the cap name; the ENV table decides.
  The universe is generated for `env_default` (superset).

## ps_1_x sampler types
ps_1_x has no dcl_samplerType: the type is the BOUND TEXTURE's. The translator must take it as an INPUT (per-stage type, part of the shader variant key).
Today only a CTAB comment carries it, so every type-dependent ps_1_x texture row without a CTAB is a failure of class "sampler type input" (no guessing a default).

## Files
`sm_legacy.tsv`: the 168 census legal=? rows resolved (legal/illegal/conflict/unreachable-doc + source). `sm_illegal.tsv`: illegal rows with the documented validator rule;
the device must return D3DERR_INVALIDCALL (later task). Baseline (RED): see `python3 -I tests/l6/test_sm_total.py --build B`.
Encoder check: sm_hand vs s6b_asm byte-identical on 475 of 478 sampled accepted rows; the 3 differ on scalar-sink (oFog/oPts) write masks.
Not covered: instruction-count limits, texm* dst index variants, predicated flow nesting, per-instruction read-port rules, `_db/_da` aliases (same tokens as _dz/_dw).
