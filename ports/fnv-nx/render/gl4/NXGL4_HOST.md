# libnxgl4.so on the host: the published library, its host packages, the nxgpu call sequence

Facts for the boot-stage renderer (verify/bootgpu.py of L4). Every statement cites the code it comes from. Paths are
relative to `ports/fnv-nx/render/d3d9/backend/` unless they start with `tests/`, `verify/` or `render/`.
"The code does not" marks a place where the library does not do what an embedder might presume.

## 1. The published library

The newest build is the fnv-nx-build branch `l6-render/nxgl4/13c4573c49d7`: one commit,
`2f0427f556c6a1d5af80d76a313a386abfc6c03a` ("L6 render nxgl4 13c4573c49d7", 2026-11-11 21:10:17 +0000). Older
builds: `l6-render/nxgl4/{f70516fe70cc,8b20825ad72b,b0be70a6f2d6,d912c3182408}`.

| path | mode | bytes | sha256 |
|---|---|---|---|
| `libnxgl4.so` | 100755 | 3840336 | `52d324caabe9322a5a46b745f5af1df73f2046c965a4b442f161cbc47b61a7a0` |
| `meta.json` | 100644 | 1708 | (describes the .so) |

`meta.json` records:
- `nx_agent_commit` `13c4573c49d7b5d316ce7aa3df9e6226471c13b7`, the sha256 above, `libgl1-mesa-dri`
  25.2.8-0ubuntu0.24.04.4, meson 1.12.1 and the ldd list.
- Defined symbols: `nxgl4_create`, `nxgl4_destroy`, `nxgl4_ext`, `nxgl4_last_error`, `nxgl4_shim_glsl`.
  Undefined, left to the embedder: `nxgl4_guest_ptr`, `nxgl4_on_present`.
- Tests gl4, drawgl, nxcheck: `ALL OK (--no-mutations)`; abi_check: exports as 8b20825ad72b, `nxgpu4.h` unchanged.

**tree_sha256 = `436df06806ab414a41b426739e18259025a1a4fe7a2dfb412cc75130d8c62b15`**

This is computed the way `verify/recipe.py:289-295` (fetch_externals) does it: fetch the exact commit, `read-tree` and
`checkout-index` into a directory, `cas.ingest` (line 294), then compare with `tree_sha256` (line 295).
- The format is `fnvnx-tree/1` (`verify/cas.py:8-16`; canon :30-32, `_entry` :110-133, ingest :135-145, tree_hash
  :147-151).
- A file entry is `{"t":"f","h":<sha256>,"x":0|1}`, where x is 1 when any exec bit is set. A directory hash is the
  sha256 of canonical `{"format":"fnvnx-tree/1","entries":…}`.
- Three computations agree: `python3 -I verify/cas.py ingest STORE DIR` (STORE first), `cas.tree_hash`, and
  `render/total/gate_d3d9total.py:104-127`, which states the same definition.

The external entry, in the form of `verify/RECIPE.md:16` and `:58-69` ("External inputs"):

```json
{"name": "nxgl4", "repo": "fnv-nx-build", "ref": "l6-render/nxgl4/13c4573c49d7",
 "commit": "2f0427f556c6a1d5af80d76a313a386abfc6c03a",
 "tree_sha256": "436df06806ab414a41b426739e18259025a1a4fe7a2dfb412cc75130d8c62b15", "at": "libnxgl4.so"}
```

`ref` is documentation only: the fetch is by exact commit (`git fetch --depth 1`). A hash mismatch turns the stage
`external:nxgl4` red (RECIPE.md:58-69).

How the source moved since the build: `git diff --stat 13c4573c49d7..66e2f971 -- ports/fnv-nx/render/d3d9/backend
ports/fnv-nx/render/shaders` is empty (0 files). 66e2f971 is the L6 head; between the two there are 9 commits and
the whole-repo diff is 25 files, +1233/−259, none of it in the backend or shaders. The published .so therefore matches
the current backend source.

The ABI headers are `nxgpu4.h` and `nx/gpu_abi.h` in this directory. `render/` is not on the trunk
`claude/fnv-nx-clean` yet, so those are the paths they will have there once L6 merges.

## 2. Host packages at run time

NEEDED (`readelf -d`): `libstdc++.so.6`, `libm.so.6`, `libEGL.so.1`, `libgcc_s.so.1`, `libc.so.6`,
`ld-linux-x86-64.so.2`. The build links `-lEGL -lGL -lm` (build_gl4.sh:18-19), but libGL does not end up in NEEDED,
presumably because of `--as-needed`.

What the code uses from the host:
- An EGL surfaceless display, `eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY)`
  (gl4_core.c:97-101), then `eglInitialize` and `eglBindAPI(EGL_OPENGL_API)` (:104-105).
- A GL 4.5 core context with `EGL_NO_CONFIG_KHR` (:106-109), made current with no surface (:111).
- Every GL entry point through `eglGetProcAddress` (:92, :113). No GLES anywhere.

Packages (`dpkg -S`, Ubuntu 24.04.5, Mesa 25.2.8-0ubuntu0.24.04.4):

| loaded file | package |
|---|---|
| libc.so.6, libm.so.6, ld-linux-x86-64.so.2 / libstdc++.so.6 / libgcc_s.so.1 | libc6 / libstdc++6 / libgcc-s1 |
| libEGL.so.1 (NEEDED, the glvnd loader) | libegl1 |
| libGLdispatch.so.0 (loaded by libEGL) | libglvnd0 |
| libEGL_mesa.so.0 and /usr/share/glvnd/egl_vendor.d/50_mesa.json (the EGL vendor) | libegl-mesa0 |
| libgallium-25.2.8-0ubuntu0.24.04.4.so (llvmpipe, GL) | mesa-libgallium |
| libLLVM.so.20.1 (llvmpipe JIT) | libllvm20 |

Measured: `nxgl4_create(16,16)` under `LD_DEBUG=files`, `tests/l6/gl4/t_preload.c` embedder, no Mesa variables,
no /dev/dri; the create succeeded.
- It loaded libEGL, libEGL_mesa, libGLdispatch, libgallium, libLLVM.so.20.1, libgbm, libdrm*, xcb/X11-xcb and
  libwayland-client.
- It loaded no `dri/*.so`, no libGL and no libGLES.
- Dependency chain: libegl1 Depends on libglvnd0 and libegl-mesa0. libegl-mesa0 Depends on mesa-libgallium, libgbm1,
  libdrm2, libwayland-client0, the xcb libraries and others. mesa-libgallium Depends on libllvm20.

Verdict:
- **Required:** `libegl1`. Name `libegl-mesa0` explicitly too; libegl1 pulls it in, but it is the vendor that
  answers.
- **Base:** `libc6`, `libstdc++6`, `libgcc-s1`.
- **`libgl1-mesa-dri` is not loaded with Mesa 25.2.8.** It ships `swrast_dri.so` and the libdril stubs, which only an
  older Mesa needs (for example noble's base 24.0, with drivers in dri/). Keeping it is harmless.
- **`libgles2` is not needed.** The code binds `EGL_OPENGL_API` (gl4_core.c:105) and never uses GLES.

Environment:
- The library reads only `NXGL4_FRAMES` (gl4_present.c:126).
- Mesa reads `LIBGL_ALWAYS_SOFTWARE=1`, `GALLIUM_DRIVER=llvmpipe` and `EGL_PLATFORM=surfaceless`, which
  tests/l6/gl4/test_gl4.py:28 sets. Set them on any host that has /dev/dri, so llvmpipe is chosen deterministically.

## 3. The nxgpu call sequence

```c
/* gl4.h:22-38 */
void*  nxgl4_guest_ptr(uint64_t addr, uint64_t size);   /* embedder: whole range in one mapping, else NULL;
                                                           size 0: non-NULL when addr is inside or at the end */
void   nxgl4_on_present(uint32_t frame);                 /* embedder: called by PRESENT after the frame file */
const NxGpuApi4* nxgl4_create(uint32_t width, uint32_t height, char* err, size_t errlen); /* NULL + err on failure */
void   nxgl4_destroy(void);
uint64_t nxgl4_ext(uint32_t fn, const uint64_t* args, uint32_t nargs);
const char* nxgl4_last_error(void);   /* g4.err (gl4_gl.c:36), set by gl4_err (gl4_gl.c:28-33) */
```

The context is current on the creating thread; every later call must come from it (gl4.h:26-27). Dispatch (gl4_core.c:77-89):
- fn 9 SHUTDOWN runs glFinish.
- fn > 19 returns `NXGPU_BAD_CALL = 0xFFFFFFFFFFFFFF02` (gpu_abi.h:191).
- fn 0 INFO needs nargs ≥ 1 and non-NULL args.
- Every other fn returns BAD_CALL when nargs is below its arity or args is NULL.

fn ids:

| range | ids | source |
|---|---|---|
| v3 | INFO 0, MEM_CREATE 1, MEM_GPU_ADDR 2, IMAGE_CREATE 3, SAMPLER_CREATE 4, SHADER_CREATE 5, SUBMIT 6, WAIT_FRAME 7, READBACK 8, SHUTDOWN 9 | gpu_abi.h:186-189 |
| v4 | IMAGE_CREATE4 10, IMAGE_DESTROY 11, MEM_DESTROY 12, SHADER_DESTROY 13, SAMPLER_UPDATE 14, IMAGE_READ 15, QUERY_RESULT 16, PRESENT 17, WAIT_VBLANK 18 | nxgpu4.h:261-266 |
| v4.1 | SHADER_CREATE_D3D 19 | gl4_core.c:20 |

**The fake library of verify/tests/test_bootgpu.py uses ids the real library does not have.** Its fn 1 sum,
2 write, 3 present, 4 ask range and 5 size do not exist as such. The real calls are mapped in the sequence table
below.

Structs (little endian, byte offsets):
- **NxInfo**, 8 × u32, filled at gl4_core.c:131-132: magic `0x5550474e` (gpu_abi.h:25), version 4,
  frames_in_flight 2, flags, width, height, backend 2, pad.
- **NxImageDesc**, 32 bytes (gpu_abi.h:41-53):
  - 0 u16 width; 2 u16 height; 4 u8 format (RGBA8 = 1, nx/state.h:174, bytes R,G,B,A); 5 u8 mips (0 = 1);
    6 u8 flags (RenderTarget = 1); 7 pad.
  - 8 u16 swizzle; 10/12 pad; 16 u64 data (guest address); 24 u32 data_size; 28 pad.
- **NxImageRead**, 48 bytes (nxgpu4.h:176-181):
  - 0 NxTarget src `{u32 image; u16 level, layer}` (nxgpu4.h:65); image 0 is the swapchain.
  - 8 u32 x, y, z, w, h, d; 32 u64 dst; 40 u32 dst_size; 44 u32 rowPitch.
- **NxPresent**, 80 bytes, size asserted (nxgpu4.h:229-240):
  - 0 u32 image; 4 i32 sx, sy, sw, sh; 20 i32 dx, dy, dw, dh; 36 u32 interval (0-4).
  - 40 u64 gamma (0 = none); 48 u32 frame; 52 pad; 56 u64 cursor (0 = none); 64 i32 cx, cy; 72 u32 cw, ch.

The shortest sequence covering L4's five cases. It starts with `nxgl4_create(W, H, err, n)` and ends with
`nxgl4_destroy()`.

| L4 case | call | what the code does |
|---|---|---|
| (a) read guest | `ext(3,[&desc],1)` IMAGE_CREATE, desc `{8,8,fmt 1,mips 1,flags 1,data=A,data_size=256}` | desc via guest_ptr (gl4_image.c:148), then guest_ptr(data, data_size) (:152); returns an id, or 0 if data_size is too small (:96-112) |
| (b) write guest | `ext(15,[&rd],1)` IMAGE_READ, `{src={id,0,0},0,0,0,8,8,1,dst=B,256,rowPitch 32}` | range checks gl4_read.c:27-42, guest_ptr(dst,dst_size) :43, memcpy :58; returns 0 |
| (c) present | `ext(17,[&p],1)` PRESENT, `{id, 0,0,8,8, 0,0,8,8, interval 0, gamma 0, frame N, cursor 0}` | validates gl4_present.c:105-113, glFinish :124, frame files if `NXGL4_FRAMES` is set, then `nxgl4_on_present(N)` :128; returns 0 |
| (d) refusal | fn 17 or fn 15 with the **descriptor** address outside guest memory | guest_ptr gives NULL, the call returns `0xFFFFFFFFFFFFFF02` (gl4_present.c:104, gl4_read.c:23) |
| (e) swapchain size | `ext(0,[&info],1)` INFO | NxInfo words 4 and 5 (gl4_core.c:132) |

Frame files (gl4_present.c:83-100, documented at gl4.h:33-37 and nxgpu4.h:183-193):
- `<NXGL4_FRAMES>/frame_%06u.rgba` is the whole swapchain image, RGBA8, top row first, `w*h*4` bytes, read with
  glGetTextureSubImage.
- `frame_%06u.txt` holds `"%u %u\n"` (width, height).
- `%06u` is `NxPresent.frame`, not a counter in the library.
- With the variable unset or empty, nothing is written. A failed write returns BAD_CALL (:127), with no on_present.

Swapchain (gl4_core.c:122-129): only when W and H are both non-zero is a zero-filled (gl4_swap_zero) RGBA8 2D
render-target image created, with the sizes cast to u16 (:125). With W or H equal to 0, PRESENT (gl4_present.c:106/109)
and WAIT_VBLANK (:133) return BAD_CALL.

### Refusals, and where the code does not refuse

Clean BAD_CALL when guest_ptr returns NULL:
- IMAGE_READ descriptor (gl4_read.c:23); PRESENT descriptor, gamma and cursor (gl4_present.c:104, 107-111).
- SUBMIT descriptor (gl4_stream.c:181-182); QUERY_RESULT out (gl4_query.c:116-117); SAMPLER_UPDATE (gl4_sampler.c:59-60).
- SHADER_CREATE_D3D descriptor, bytecode and link set (gl4_d3dshader.c:31-32, 39-41).

Return 0 (no id) instead: IMAGE_CREATE descriptor (gl4_image.c:149), IMAGE_CREATE4 (:162-163), MEM_CREATE
(gl4_core.c:29), SAMPLER_CREATE (gl4_sampler.c:49-50), SHADER_CREATE (gl4_shader.c:116-117). These refusals set no
message, so `nxgl4_last_error` keeps the previous text.

**The code does not check these pointers**; a NULL from guest_ptr is dereferenced or silently accepted:
- INFO out: memcpy into NULL (gl4_core.c:60-61).
- IMAGE_READ dst: unchecked at gl4_read.c:43, memcpy at :58.
- READBACK dst: passed to glReadPixels (gl4_read.c:94).
- IMAGE_CREATE data: NULL means "no upload", and a valid id comes back (gl4_image.c:152).
- SHADER_CREATE glsl (gl4_shader.c:118).
- SUBMIT stream (gl4_stream.c:170): stream_ok dereferences it when words > 0.
- The ImageUpdate source (gl4_image.c:197) and `gl4_upload_range` (gl4_core.c:55).

So an out-of-range test must aim a descriptor outside guest memory; an out-of-range dst or data buffer does not
return 0xFFFFFFFFFFFFFF02 (the process crashes or the call succeeds). Also: MEM_CREATE does not validate its storage
range at create time (gl4_core.c:27-40), and MEM_GPU_ADDR always returns 0 (gl4_core.c:41).

### The reference test

`tests/l6/gl4/test_gl4.py:67-109` (ctypes_child):
- The embedder is `tests/l6/gl4/t_preload.c:5-6`, loaded RTLD_GLOBAL: guest_ptr is the identity, on_present counts.
- argtypes :72-73; `nxgl4_create(16,16,err,256)` :75; INFO checks magic, version 4, backend 2 :81; fn 3 `Desc(8,8,1,1,1)` :82-86.
- SUBMIT stream `3|8<<16, img, 0, 1, 0xFFC08040, 0,0,8,8, 4, 0`, a clear pass then End :88-91.
- `Pres(img,0,0,8,8,0,0,16,16,1,gamma,frame,0)` through fn 17, checking the `.rgba` bytes and `.txt` `16 16\n`
  :98-103; hook count :104.
- An out-of-bounds rect returns BAD_CALL without the hook :106; fn 99 returns BAD_CALL :107; destroy :108.
- Range-checking embedders: `tests/l6/selftest/harness.c:17-24`, `tests/l6/present/server.c:9-16`.
