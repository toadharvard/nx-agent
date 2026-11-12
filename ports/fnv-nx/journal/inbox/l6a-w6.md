# l6a-w6: OpenGL backend as product root render/gl4

Branch claude/fnv-l6a-deko-w6. Base: trunk claude/fnv-nx-clean plus a merge of origin/claude/fnv-l6a-deko-w4, because the trunk has no render/backend root yet (gl4 includes nxgpu4.h, nxgpu4_shader.h, nxgpu4_check.h from it).

Root ports/fnv-nx/render/gl4: 17 gl4_*.c, gl4.h, gl4_funcs.h, libnxgl4.map, NXGL4_HOST.md, byte-for-byte from fnv-l6-render 7f8581af. build.json: x86_64-linux-gnu-gcc-13, flags -std=c11 -Wall -Werror -Werror=switch-enum -O2 -fPIC, include gl4 + render/backend, link null. admit.json entry: product, host, same gate list as render/backend.

## Result
- recipe.py check --root ports/fnv-nx/render/gl4: valid.
- envcheck: OK.
- recipe.py build (twice, fresh stores): RED before any unit is compiled, both times, identical: `recipe: error: --exe /home/user/nx-agent/as is not a file`.
  Units compiled: 0/17 (+0 nxgpu4_check.c, see gap 3). Determinism: not measurable (no objects).
- L1 verdict: not run, the root cannot build.

## Machinery gaps (not worked around)
1. x86_64 host compiler: `x86_64-linux-gnu-gcc-13 -print-prog-name=as` prints bare `as` (native driver finds it on PATH). unitstage.py (norm(q("-print-prog-name=as")), line ~97) turns it into cwd/as, which is not a file. The aarch64 cross drivers print full paths, which is why render/backend builds. recipe.py already knows this for plugins (line ~623: "a native driver prints a bare as/ld") but unitstage does not. L1 must resolve a bare name against /usr/bin (env.json binutils) in unitstage. Owner: L1.
2. Shared object link: `link` allows only {script, output NAME.elf, flags} via `ld -T script`. No -shared, no --version-script, no .so output. link is null; libnxgl4.so cannot be produced by the recipe. Owner: L1.
3. One root linking another root's object: the recipe has no key for it. nxgpu4_check.c is a unit of render/backend; the GL image needs its object. Not copied. L1 must offer a cross-root object input (an `objects_from: root` of an admitted root), or the file moves into a shared root.

## Blockers known in advance (when the compiler gap is fixed)
- EGL/GL headers (EGL/egl.h, EGL/eglext.h, GL/glcorearb.h) are not pinned in env.json (/usr/include/EGL is absent here). gl4.h includes them, so all 17 units that include gl4.h are red: gl4_blit gl4_check gl4_core gl4_d3dshader gl4_fmt gl4_gl gl4_image gl4_pass gl4_present gl4_query gl4_read gl4_sampler gl4_scale gl4_shader gl4_state gl4_stream gl4_vtx. Class: missing GL headers. Owner: L1 (pin libegl-dev, libgl-dev) and add the include dirs.
- fnvnx_shaders.h: gl4_d3dshader.c includes it; it comes from L6 render/shaders (w1b). Not present, so that unit is red a second time. Class: missing translator. No include entry added for it.
- libfnvnx_shaders.a, libmojoshader.a, libuam.a: link-time only; link is null.

## Update after lead's L1 answers (w15/w16 not on any pushed branch yet)
build.json link now written to the announced keys: {"kind":"shared","output":"libnxgl4.so","version_script":"libnxgl4.map","libs":["libEGL.so.1","libGL.so.1"],"roots":["ports/fnv-nx/render/nxgpu@x86_64"],"flags":[]}.
Key names beyond kind/version_script are my reading of the lead's text; w15's schema is not visible yet, so recipe check is the expected red until w15/w16 land. Re-check then and adjust key names only.

## Translator root (head 1bbfd5c)
- The lead named render/shaders/glsl @ 7ae3ac87. On branch claude/fnv-l6-render-w1b that root was renamed to render/shaders/translator at 8ce32e8 (Q6). I merged the branch head 8ce32e8 and used render/shaders/translator (include dir .../translator/include, link.roots .../translator). The branch's admit.json has no entry for the translator root; I kept my gl4/backend entries in the merge conflict.
- Source edit, one commit alone: gl4_d3dshader.c line 58 fnvnx_shader_translate( -> fnvnx_shader_translate_glsl(, same arguments.
- Proof so far: the source has 0 `fnvnx_shader_translate(` calls. The PP output cannot be produced yet (gcc -E stops at missing GL/glcorearb.h, w16). Note: PP output will still contain the declaration of fnvnx_shader_translate from fnvnx_shaders.h (line 90), so "0 references" must mean 0 call sites, not 0 textual matches.
- Undefined-symbols subset check: after the build, not done.

link.libs = ["libEGL.so.1"] only (lead: every GL entry point loads through eglGetProcAddress; L4's pinned run-time set has no libGL). L1 ldcheck proves it; a direct gl* call would be reported by unit and symbol, not fixed with libGL.

## Compile result with L1 w16 b57061c8 (scratch merge, link null; head of the real branch is not changed by w16)
- recipe check: valid after dropping the in-root include (w16 rule: "include inside the root: use a quoted relative include"; the sources already use quoted includes). Same drop applied on this branch.
- Two fresh-store builds: green both, 17/17 units compiled, 0 red, stages 34 run / 0 hit. Object sha256 of all 17 units equal between the two builds: deterministic.
- Undefined set of the root: fnvnx_shader_hash, fnvnx_shader_result_free, fnvnx_shader_translate_glsl; fnvnx_shader_translate is absent. egl: eglBindAPI eglCreateContext eglDestroyContext eglGetError eglGetProcAddress eglInitialize eglMakeCurrent eglTerminate; no direct gl* (non-gl4) symbol. So libGL is not needed.
- Not yet done: link (waits for w15), "defined by the translator root" (translator root not built here), L1 verdict.
- Fix: my earlier merge of w1b left conflict markers in CLAUDE.md (I only resolved admit.json). Removed in e7f28e6. Those markers were only on my branch.
