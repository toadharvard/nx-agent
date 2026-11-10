# D3D9 fixed-function vertex pipeline (T-FFP-VS, rule RD5)

One program, behaviour selected by data: `ffp.vert.glsl` (build `NX_FFP_FLAT` for flat shading, like the pixel stage) and its CPU twin
`ffp_vs_ref.c` read `NxFfpVsState` (`ffp_vs_state.h`: std140, 2144 bytes, UBO binding 4) + the world palette (`mat4 world[256]`, 16384
bytes, binding 5) + `posfix` of driver UBO 2. `nx_ffp_vs_state_from_d3d9` is pure; a 256-mat4 block compiles in uam (its limit is 65536
bytes = 1024 mat4; 1025 is rejected "too big (65600/65536)"). Test: `python3 tests/l6/ffp_vs/test_ffp_vs.py`.
Interface: attributes POSITION 0, BLENDWEIGHT 1, BLENDINDICES 2, NORMAL 3, COLOR 4-5, TEXCOORD 6-13 (= fnvnx_dk_attrib_location), PSIZE 14;
tweening (VERTEXBLEND 255, no weights) reuses 1 = POSITION1, 2 = NORMAL1. Absent inputs: presence bits in the block, the bound value is ignored.
Varyings COLOR0 0, COLOR1 1, TEXCOORDn 2+n, fog 18 (.x). Matrices are D3D row-major bytes (GLSL sees the transpose, writes `M * v`).
Not vertex work: D3DRS_WRAPn (texture-coordinate wrapping happens in the rasteriser/sampler; the host must set the sampler/interpolation
mode), frustum clipping, MaxPointSize, viewport depth range (POSITIONT z is used as is).

## Domain functions (every enumeration value has one meaning; wined3d only logs FIXME for most)
VERTEXBLEND: 1,2,3 = n weights, 255 = tweening, 256 = 0 weights, everything else = DISABLE. Indexed blending applies for 1,2,3,256 only.
Material source: 1 = COLOR1 (vertex diffuse), 2 = COLOR2 (vertex specular), anything else = MATERIAL. FOGTABLEMODE/FOGVERTEXMODE outside 1..3 =
NONE. Light type outside 1..3 = no contribution (SetLight rejects it). TCI high word 1..4 = texgen, else PASSTHRU; low word >= 8 = absent set.
TTFF: count = low byte (1..4 transforms, else none), projected = bit 8. Component counts > 4 read as 4. Palette index: floor(x*scale+0.5) clamped to
0..255 (scale 255 for D3DCOLOR indices, 1 for UBYTE4; memory byte order, no swizzle). Singular matrix: inverse (transpose) -> adjugate (transpose).
x/0 cases: zero attenuation denominator -> att 0; spheremap m = 0 -> (0.5,0.5); LINEAR fog end == start -> step; PSIZE denominator <= 0 -> MAX;
MIN > MAX -> MAX; w == 0 after view -> no divide; viewport size 0 -> 0; rhw == 0 -> w = 1. Float NaN/Inf inputs propagate (IEEE), not domain.

## Decisions where the docs are silent (model = wined3d_ffp_get_vs_settings, shader_glsl_generate_ffp_vertex_shader,
## shader_glsl_ffp_vertex_lighting, compute_texture_matrix unless noted)
1. Defaults: diffuse (1,1,1,1) (wined3d), specular (0,0,0,0) (task text; wined3d's GL default would be alpha 1), normal 0 (= wined3d "no normal":
   only ambient), PSIZE = D3DRS_POINTSIZE, texcoord components beyond the declared ones (0,0,0,1) (GL defaults, as wined3d).
2. POSITIONT: clip = (ndc*w, w), ndc = (2(X-vx)/vw-1, 1-2(Y-vy)/vh, Z), w = 1/rhw. No half-pixel in the formula: posfix is applied afterwards
   exactly as for translated VS (so wined3d's own +1/w centre offset is NOT used). No lighting, normal = 0, no texture matrix, no user planes; ec = (X,Y,Z,1).
3. Blending: weights beyond the declared count are 0, last weight = 1 - sum; eye = sum w_i * (p W_i V); normal = n * inverse-transpose(W0 V) for one
   matrix, else sum w_i * n * (W_i V)3x3 (wined3d; no inverse transpose per palette matrix). Tweening: lerp(POSITION, POSITION1, TWEENFACTOR), same for normals.
4. Lights: first NX_FFP_MAX_LIGHTS = 8 of the ENABLED lights in enable order (wined3d lights[] slots); lights are put into eye space by the CPU
   (position * V, directions through V's 3x3, normalised; directional stores the vector TO the light). Eye position is divided by w first.
5. Lighting (wined3d non-legacy = D3D9): range: distance > Range -> light ignored incl. ambient; att = 1/(a0+a1 d+a2 d^2); spot rho > cos(theta/2) -> 1,
   <= cos(phi/2) -> 0, else ((rho-cos(phi/2))/(cos(theta/2)-cos(phi/2)))^falloff, att *= spot; directional: att 1. Specular H = normalise(L + V),
   V = -normalise(eye) (LOCALVIEWER) or (0,0,-1); added if N.L > 0 and N.H > 0: pow(N.H, power) * att * light.specular (all 4 components, so the
   specular ALPHA = material specular alpha * sum; wined3d). color0 = A*(ambient + sum) + D*sum + E, alpha = D.a. SPECULARENABLE off: color1 = vertex COLOR1
   (wined3d) - D3D's real behaviour may be 0. Everything saturated. LIGHTING off (or POSITIONT): colour0/1 = vertex colours (or defaults).
   COLORVERTEX off, or a source colour missing from the declaration: MATERIAL (wined3d validate_material_colour_source).
6. Texture matrix: the input set of n components is padded with 1 in component n and 0 after (D3D FFP); n = 3 for texgen (wined3d), n = 0: (0,0,0,1). wined3d
   fixes the matrix up (copies row n to row 3) - same result. DIFFERENCE: wined3d uses the identity for COUNT1 (and any count outside 2..4); here COUNT1 transforms
   and keeps x (D3D docs). Components >= COUNT are 0; PROJECTED copies component COUNT-1 into w (pixel stage divides by it). Texgen: CAMERASPACENORMAL/POSITION/
   REFLECTIONVECTOR/SPHEREMAP as wined3d, from the (optionally normalised, NORMALIZENORMALS) camera-space normal.
7. Fog (location 18 .x): FOGENABLE off = 1.0; table fog on (FOGTABLEMODE 1..3): eye |z| (POSITIONT: Z) for the pixel epilogue, which computes the factor; else
   vertex mode 1..3: depth = |z| or length(eye) (RANGEFOGENABLE), EXP/EXP2/LINEAR saturated; else specular alpha of COLOR1 (POSITIONT too). No ortho special case.
8. Point size: Ss = Vh*Si/sqrt(A+B*De+C*De^2) with POINTSCALEENABLE, else Si (PSIZE input or POINTSIZE), then clamp to [MIN, MAX]; De = |eye|.
9. User planes: world-space planes arrive as V^-1 * plane (CPU); distance = dot(eye before the w divide, plane) when CLIPPING and the enable bit; else +1.
10. gl_Position = clip; xy += posfix.xy*w; y *= posfix.z (README "D3D9 conventions"); depth stays in 0..w.
