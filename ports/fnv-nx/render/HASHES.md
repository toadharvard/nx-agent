# render/HASHES: FNV-1a (and other non-cryptographic hashes) as content keys, and the sha256 implementations available (L6, worker w5)

Static census, nothing run or changed. Measured at commit f66de58e (`claude/fnv-l6-render-w5` merged with `claude/fnv-l6-render` f66de58e). Method: `git grep` over `render/**` and `tests/l6/**` for the FNV constants (32 and 64 bit, hex and decimal), the names fnv/crc/murmur/djb/adler, and every definition or use of a `*hash*` function; each hit read. Root membership: `render/d3d9` = product root (guest, ILP32; its recipe `units: all` takes every .c/.cpp of the directory, so every `render/d3d9/dev/*.cpp` is in the closure); `render/shaders` = the translator root (host, C/C++; `render/shaders/cache/shcache.c` is also compiled ILP32 per its test); `render/gl4` and `render/backend` = backends (host LP64 / console); `tests/l6` = tests (host).

## 1. Every non-cryptographic hash in render/** and tests/l6/**

| # | file:line | hash | what it keys / is for | root | content key? |
|---:|---|---|---|---|---|
| 1 | `render/shaders/src/translate.c:13-18` `fnvnx_shader_hash` (declared `render/shaders/include/fnvnx_shaders.h:95`, `render/backend/nxgpu4_shader.h:12`) | FNV-1a 64 | the code hash of the D3D9 shader bytecode: `NxShaderD3D.hash` (the ABI field) and the shader cache key; used at translate.c:60, `sdp2dksh.c:85,107`, `frontend.c:184`; **verified** by the GL backend `render/gl4/gl4_d3dshader.c:51` (descriptor hash must equal it) | translator (and gl4 as the user) | **yes** |
| 2 | `render/d3d9/dev/res_shader.cpp:12-15` `nx_shader_hash` (declared `res_core.h:44`) | FNV-1a 64, a re-implementation of #1 ("== fnvnx_shader_hash", comment at :6) | the same code hash, computed by the device when a shader is created (:33 `s->hash`), sent at `draw_prog.cpp:49` (`d->hash = s->hash`) | **render/d3d9 product (guest ILP32)** | **yes** |
| 3 | `render/shaders/cache/shcache_key.c:6-11` `shcache_hash64` (`shcache.h:65` "FNV-1a 64: equals fnvnx_shader_hash") | FNV-1a 64, a third copy | main-file body hash (`shcache.c:47`); basis of #4 | translator root (cache loader LP64 and ILP32, builder) | integrity checksum |
| 4 | `render/shaders/cache/shcache_key.c:13-19` `shcache_l_hash` (uses at `shcache.c:53,64,80,112`) | FNV-1a 64 of the link-set pairs, 0 for the empty set, else nonzero | the L component of the cache KEY (code hash, stage, flags, L hash, code length) and of the index order | translator root | **yes** (key part) |
| 5 | `render/shaders/cache/shcache.c:118-119` `rec_chk` | FNV-1a 64 step loop inline (length seeded through #3) | per-record checksum of the append-only LOG (torn record detection) | translator root | integrity checksum |
| 6 | `render/shaders/cache/mkshcache.py:27,33,70,77` `fnv1a64`, `l_hash` | FNV-1a 64 (Python) | builder: key code hash, L hash, body hash (the cache builder and reader must equal #3/#4/#5) | host tool | **yes** |
| 7 | `render/shaders/cache/d9s_shaders.py:22` `fnv1a64` | FNV-1a 64 (Python) | extractor: `<hash>.bin` file name = key (docstring :4 "key = fnvnx_shader_hash") | host tool | **yes** |
| 8 | `render/tools/rec/farm/d9rec_stats.py:29,66,137` `fnv1a64` | FNV-1a 64 (Python) | shader hash for the per-frame histogram of a recorded stream ("as tools/compare"), report field `"hash": "fnv1a64"` | recorder farm tool | report key |
| 9 | `render/d3d9/dev/draw_ids.cpp:14` `fnv` | **FNV-1a 32** | bucket index of the in-memory layout and state tables (`g_head[hash % NBUCKET]`, :16,37,41); every hit is confirmed by `memcmp` of the whole key (:27, :37) | **render/d3d9 product** | **no**: a bucket only, collisions harmless |
| 10 | `render/gl4/selftest/nxgpu4_selftest.c:60,75` (and `tests/l6/selftest/test_selftest.py:33,47`) | FNV-1a 64 | checksum of the rendered scene readback (S3/S4) compared with `selftest_expect.h` | gl4 selftest / its test (also built for console) | no: output checksum |
| 11 | `tests/l6/draw/t_e.cpp:4` `fnv64`; `tests/l6/ffpdev/t_ffp.cpp:74` `fnv64` (decimal offset 1469598103934665603); `tests/l6/ffpdev/model.py:27-29` `fnv64` | FNV-1a 64 | test copies: expected program/palette hashes | tests | test oracle copy |
| 12 | `tests/l6/res/t_shader.cpp:46`; `tests/l6/gl4/t_parts_q.c:11,86` | uses #2 / #1 | checks `nx_shader_hash` against the FNV test vectors (`""` = 0xcbf29ce484222325, `"a"` = 0xaf63dc4c8601ec8c), builds descriptors | tests | no |
| 13 | `tools/compare/capstore.py:62`, `perm.py:86,184` (outside render/, via `nxd9io.fnv1a64`) | FNV-1a 64 | the shader's name `%016x.glsl` in the Wine-recording tools | tools | key (outside this census' scope, listed) |

Not hashes of content (data generators), excluded: `tests/l6/present/t_parts.cpp:7` (a 64-bit finalizer for pixel patterns), `tests/l6/gl4/t_main.c:37` `t_hash` (a PRNG), `tests/l6/wrap/plan.py:14` / `gen_echo.py:133` and `tests/l6/reclock/test_reclock.py:43` (`s*31 + j*7` arithmetic fill patterns). No CRC, murmur, djb2, adler or siphash anywhere in the two trees. `render/d3d9/ffp/gen_glsl.py:46` already uses `hashlib.sha256` (the FFP program key, test_ffp_glsl_key).

### Counts (all of render/** and tests/l6/**, commit f66de58e)

- FNV-1a implementations: **C/C++ 8** (#1, #2, #3, #5, #9 (32-bit), #10, and the two test copies of #11), **Python 5** (#6, #7, #8, #10's test_selftest.py, model.py), 13 in all, plus `tools/compare` outside the two trees.
- Used as a **content key** (a cache or ABI key derived from content): code hash 3 times in C (#1, #2, #3 as the same function), 3 times in Python (#6, #7, #8), link-set hash 2 (#4 C, #6 Python) = **8 key sites; 5 of them are the one function written five times** (C: #1, #2, #3; Python: #6, #7).
- Integrity checksums (not keys): body hash and log record checksum (#3 use, #5, #6) = 3 sites.
- Bucket only (stays): 1 (#9). Output checksum: 2 (#10). Test copies / oracles: 4.
- By root: **render/d3d9 product: 2 hash sites (#2 content key, #9 bucket)**; translator root: #1, #3, #4, #5 (4 C sites) + #6, #7 (2 Python); backends: #1's verifier in gl4 (1), #10; tests: #11, #12.
- The code hash is the ABI value `NxShaderD3D.hash` (`nxgpu4_shader.h:31`, `fnvnx_shaders.h:85`): a producer (#2, product) and a verifier (#1 in gl4_d3dshader.c:51, test_gl4 mutation "if (0)" at `test_gl4.py:178`) must agree, and `shcache.h:19,22,49,65,66` and `PLAN_DK.md:48,107` document it as FNV-1a 64.

## 2. sha256 implementations available

| side | implementation | where | API | notes |
|---|---|---|---|---|
| guest (ILP32, the product and the console) | **`fnvnx_sha256`**, portable C99, FIPS 180-4, no asm, no libc beyond `internal.h` | `loader/src/sha256.c:35`, `loader/include/fnvnx_loader.h:119` | `void fnvnx_sha256(const void *data, size_t size, uint8_t out[32])` (one shot) | built by `loader/Makefile` with `-Wall -Wextra -Wshadow -Wconversion -Werror`; target `ilp32-check` compiles it for `aarch64 -mabi=ilp32`; ASan/UBSan on the host; used by the loader (`loader/src/pe.c:156`, code fingerprints; test oracle `hashlib` in `loader/tools/fnvnx_pe.py:167`). Not a dependency of any render root today. It sits in the loader tree, not in render/d3d9 (the loader is a separate root, not admitted on this branch's `verify/admit.json`) |
| guest, alternative | libnx `sha256CalculateHash` / `sha256ContextCreate/Update/GetHash` | pinned `tools/libnx` 518bf11a: `nx/include/switch/crypto/sha256.h:28-35`, `nx/source/crypto/sha256.c` | one-shot and streaming | uses ARMv8 crypto-extension inline asm (`sha256h`, `sha256h2`, `sha256su0/1`, sha256.c:54-78): aarch64 only, and the L4 emulator would have to implement those instructions; on the console it is the hardware path (used in `toolchain/host/host.c:367`) |
| host (translator and tools, C) | **none of its own**: the translator (`render/shaders`: translate.c, sdp2dksh.c, frontend.c, shxlate.c, shcache*.c) links no sha256 and no libcrypto (build.sh / CMakeLists: no crypto dependency). `loader/src/sha256.c` compiles for the host (the loader's own host build `loadcheck`), so it is the one available host C implementation | `loader/src/sha256.c` | same | the host build of the loader library is `libfnvnx_loader.a` |
| host (Python tools) | `hashlib.sha256` (stdlib) | `render/d3d9/ffp/gen_glsl.py:46`, `verify/cas.py`, loader tools | | already used for the FFP key; the owner-accepted assumption 2 is SHA-256 itself |

So: **one portable C implementation exists for both sides, `fnvnx_sha256` (loader/src/sha256.c)**; libnx's is a second guest implementation with an instruction-set requirement; the host Python side has `hashlib`. The translator and the cache C code have no sha256 today, and the product root `render/d3d9` has none either (it would need the loader's file as a dependency, or a copy, which would be a second implementation).

## 3. What a change touches (for the lead's decision, no change made)

- The key is a 64-bit value in the ABI (`uint64_t hash`, shcache `code_hash` u64, file names of 16 hex digits). A sha256 key is 32 bytes: `NxShaderD3D.hash`, `fnvnx_shader_hash`'s signature, the cache index entry (36 bytes, `shcache.h:20`), the L hash, the file names and `NxShaderDesc.key` (already 32 bytes, FFP) are the places that carry it. `NxShaderDesc.key` is the existing 32-byte sha256 slot; the D3D shader path is the one still at 8 bytes.
- Writing sites to change if the key becomes sha256: 5 content-key sites listed above (#1, #2, #3 are one function in C; #6, #7 one in Python), plus the verifier #1 in `gl4_d3dshader.c:51`, plus #4 (L hash) and #8 (statistics) if they follow, plus the test copies of #11.
- The integrity checksums (#3 body, #5 record) and the bucket hash (#9) are not content keys; whether they follow is a separate decision.

## 4. Status: content keys are SHA-256 (branch `claude/fnv-l6-render-w5-key`)

Content-key rows #1-#8 and #12 are converted to `fnvnx_sha256` (`lib/sha256`) / `hashlib.sha256`; the FNV code is deleted at those sites.

| row | now |
|---:|---|
| 1 | `fnvnx_shader_result.key[32]`, `fnvnx_shader_key_hex`; `fnvnx_shader_hash` deleted; `NxShaderD3D.key[32]` (ABI header `render/backend/nxgpu4_shader.h`, struct 64 bytes; the header is L6A w4's) |
| 2 | `res_shader.cpp`: `fnvnx_sha256(bc, n, s->key)`; `nx_shader_hash` deleted; `render/d3d9/build.json` include gains `lib/sha256/include` |
| 3, 5 | `shcache.c`/`shcache_key.c`: body check, record check = SHA-256; `shcache_hash64` deleted; cache format v2 (header 80, index entry 84, log record fixed part 78) |
| 4 | `shcache_l_key`: 32 bytes, zeros for the empty set, else SHA-256 of the pairs; `shcache_l_hash` deleted |
| 6, 7 | `mkshcache.py`, `d9s_shaders.py`: `hashlib.sha256`; file names are 64 hex digits |
| 8 | `d9rec_stats.py`: `hashlib.sha256` hexdigest, report field `"hash": "sha256"` |
| 12 | `gl4_d3dshader.c` verifies `memcmp(SHA-256(code), d.key)`; tests use `fnvnx_sha256` |

Not content keys, unchanged: #9 (bucket, memcmp-verified), #10 (scene readback checksum), #11 (test palette/program checksums). Outside render/tests/l6 and unchanged: row #13 and the `tools/compare`, `toolchain/nro`, `tests/nx1`, `tests/s6b/census.py`, `tests/s16` users of `nxd9io.fnv1a64` / `shaders_nx.fnv1a64`, which name recorded files, not cache keys.

Open: `lib/sha256` is not yet a link root of `render/d3d9` (its `link` is null; `link.roots` is L1 w15). Until then d3d9 reaches `fnvnx_sha256` through the include only; the body comes from the root that links d3d9.
