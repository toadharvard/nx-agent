# l6a-w5: uam survey — PAUSED, PARTIAL (stopped by lead; no builds were run, no product edits)

Mirror: fnv-nx-build tools/uam, commit 5a5afc2bae8b55409ab36ba45be63fcb73f68993 (README says uam 1.1.0, mesa 19.0.8 base).

## 1. Build (answered)
- meson.build:1-86: languages c (c99) + c++ (c++11), meson, buildtype release. Sources: source/*.cpp,c (compiler_iface, glsl_frontend, main, mini-os.c, tgsi_support) + mesa-imported/{codegen,tgsi,util,cso_cache,glsl,compiler,program,state_tracker,main}.
- Tools needed at build time that are NOT in verify/env.json packages: meson, ninja, bison, flex, python3 `mako` (meson.build:10-16 errors without it; also distutils StrictVersion, gone in Python 3.12). env.json has make, python3-pil, libpython-dev, zlib; no bison/flex/mako/meson/ninja.
- Generated sources (mesa-imported/glsl/meson.build custom_target at lines ~2-39 and ~149-163): bison on glsl_parser.yy and glcpp/glcpp-parse.y; flex on glsl_lexer.ll and glcpp/glcpp-lex.l; ir_expression_operation.py (enum/constant/strings; mesa python generator, uses mako). Prebuilt outputs are not in the mirror.
- Libraries: none external (no zlib, no libdrm); only libc/libstdc++. All other source is in the mirror.
- Plan for a recipe product root ("target": "host"): add bison, flex, python3-mako (and drop meson: units are plain .c/.cc) to env.json/setup_verdict_env.sh (owner-pinned set, so an env.json change, L1). Run the 5 generators as `generated` steps (bison/flex are not python: the recipe's `generated.tool` must be a tracked .py, so a small .py wrapper per generator running bison/flex is needed, or recipe.py extended for a pinned-tool generator). Flags from meson.build: -DPACKAGE_STRING, -DDESKTOP, -D_USE_MATH_DEFINES, -D_GNU_SOURCE, -DHAVE_POSIX_MEMALIGN, -ffunction-sections, -Wno-class-memaccess -Wno-non-virtual-dtor, -Werror=implicit-function-declaration (C). Units must be "all": mirror trees contain files not meant to be compiled (check against the root rule) so the product root must be a curated copy.

## 2. Encoder (partial)
- mesa-imported/codegen/nv50_ir_emit_gm107.cpp (4618 lines), class CodeEmitterGM107 (line 31). Primitive: emitField(b, s, v) at ~line 233 ORs v into a 64-bit word at bit b width s; emitInsn(hi) (line 249) zeroes code[0], sets code[1]=hi (top 32 bits = opcode), then emitPred() (bits 16..19).
- 121 emitXXX() functions; emitInstruction (line 3362) is a switch on insn->op calling them. 149 emitInsn(0x...) call sites with constant top words (e.g. emitFADD: reg 0x5c580000, cbuf 0x4c580000, imm 0x38580000, long imm 0x08000000, lines 1254-1300); a few use a conditional (line 2195/2199 SHL/SHR). Operand fields are emitGPR(pos), emitPRED, emitCBUF(buf,gpr,off,len,shr), emitIMMD(pos,len), emitSAT/ABS/NEG/CC/FMZ(pos) with literal bit positions; also `code[1] ^= const` toggles (line 1283).
- Not finished: whether a mechanical, class-closing extraction exists. Observations: operand-position arguments are literal ints in every call, so a clang-AST pass (clang 18 is in env.json) over emitXXX bodies could collect (emitInsn constant, emitField/emit* positions) per control-flow path; helpers (emitCond3/4, emitRND, emitO, emitX ...) fold more fixed/variable bits and need to be walked by the same pass. Not covered by that rule: values that depend on IR modifiers computed at run time (e.g. `code[1] ^=`, switch on insn->subOp/type tables) — these enumerate by path but the full value set needs the IR domain; a harness driving the emitter over every op/type combination is the alternative and its closure depends on the IR enum domain. No proof written.

## 3. DKSH layout (answered, source/compiler_iface.cpp OutputDksh 625-673, source/dksh.h)
- Header at 0: DkshHeader {magic 'DKSH', header_sz=24, control_sz, code_sz, programs_off=24, num_programs=1} (dksh.h 13-20); then DkshProgramHeader (64 bytes, dksh.h 31-84). control_sz = Align256(24+64) = 0x100.
- Code section begins at file offset control_sz (0x100). Non-compute: first 0x80-sizeof(NvShaderHeader)=0x30 bytes padding string, then 0x50-byte NvShaderHeader (nv_shader_header.h:250), so Maxwell code starts at section offset 0x80 = file offset 0x180; entrypoint field = 0x30. Compute: code at section offset 0, file 0x100, entrypoint 0.
- Code: 8-byte instructions with a scheduling qword every 4th slot (group of 3 insns + 1 sched word, 21 bits each); padded with NOP 0x50b0000000070f00 and a BRA $ to a multiple of 8 qwords (RetrieveAndPadCode 318-351). code_sz = Align256(0x80 + codeSize) + Align256(dataSize); constbuf1 follows at Align256(0x80+codeSize).
- First instruction of the first non-compute program: file offset 0x180 is a sched word, the first insn at 0x188.

## 4. Tracked pairs (answered; from origin/claude/fnv-l6a-deko 88a9e1d1f)
ports/fnv-nx/render/d3d9/backend/nx/: build_msres_dksh.sh -> dk_msres_dksh.h; build_scale_dksh.sh -> dk_scale_dksh.h. Command: `UAM=<uam> sh build_*_dksh.sh [out.h]` in that dir.
- msres: `uam -s vert -o vs.dksh dk_msres_vs.glsl`; `uam -s frag -o <n>.dksh dk_msres_fs_<n>.glsl` for n in avg depth stencil; stderr dropped.
- scale: dk_scale_fs.glsl is preprocessed per kind k=0,1,2 (line 1 kept, `#define KIND k` inserted, rest) into fs<k>.glsl; vs from dk_scale_vs.glsl; frag stage for fs0..2.
- Header = first line `/* generated by ...: sources <first 16 hex of sha256 of the concatenated .glsl> */`, then per program `enum { X_CTL = <header u32 @8>, X_CODE = <u32 @12> };` and `static const uint8_t x[] = {` bytes via `od -An -v -tu1 -w32` as comma lists `};`. Byte-for-byte reproduction needs the same uam binary (5a5afc2b), plus od/sed/sha256sum/cut from coreutils.
- Other glsl users, not covered: selftest/ff_{vs,fs}.glsl (selftest/build_dksh.sh), ffp/*.glsl.

## Not done
Build of uam, the instruction-form extractor, determinism check of uam output.
