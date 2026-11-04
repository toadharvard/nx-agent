/* V6n scene names: D3D9 enum names (d3d9types.h numbering) for the scene text format. */
#ifndef V6N_NAMES_H
#define V6N_NAMES_H
#include <stdint.h>
typedef struct V6nName { const char* name; uint32_t value; } V6nName;
#define FCC(a, b, c, d) ((uint32_t)(a) | (uint32_t)(b) << 8 | (uint32_t)(c) << 16 | (uint32_t)(d) << 24)

static const V6nName V6N_RS[] = {
    {"ZENABLE", 7}, {"FILLMODE", 8}, {"SHADEMODE", 9}, {"ZWRITEENABLE", 14}, {"ALPHATESTENABLE", 15},
    {"LASTPIXEL", 16}, {"SRCBLEND", 19}, {"DESTBLEND", 20}, {"CULLMODE", 22}, {"ZFUNC", 23}, {"ALPHAREF", 24},
    {"ALPHAFUNC", 25}, {"DITHERENABLE", 26}, {"ALPHABLENDENABLE", 27}, {"FOGENABLE", 28}, {"SPECULARENABLE", 29},
    {"FOGCOLOR", 34}, {"FOGTABLEMODE", 35}, {"FOGSTART", 36}, {"FOGEND", 37}, {"FOGDENSITY", 38},
    {"RANGEFOGENABLE", 48}, {"STENCILENABLE", 52}, {"STENCILFAIL", 53}, {"STENCILZFAIL", 54}, {"STENCILPASS", 55},
    {"STENCILFUNC", 56}, {"STENCILREF", 57}, {"STENCILMASK", 58}, {"STENCILWRITEMASK", 59}, {"TEXTUREFACTOR", 60},
    {"CLIPPING", 136}, {"LIGHTING", 137}, {"AMBIENT", 139}, {"FOGVERTEXMODE", 140}, {"COLORVERTEX", 141},
    {"CLIPPLANEENABLE", 152}, {"POINTSIZE", 154}, {"POINTSIZE_MIN", 155}, {"POINTSPRITEENABLE", 156},
    {"POINTSIZE_MAX", 166}, {"COLORWRITEENABLE", 168}, {"BLENDOP", 171}, {"SCISSORTESTENABLE", 174},
    {"SLOPESCALEDEPTHBIAS", 175}, {"TWOSIDEDSTENCILMODE", 185}, {"CCW_STENCILFAIL", 186},
    {"CCW_STENCILZFAIL", 187}, {"CCW_STENCILPASS", 188}, {"CCW_STENCILFUNC", 189}, {"COLORWRITEENABLE1", 190},
    {"COLORWRITEENABLE2", 191}, {"COLORWRITEENABLE3", 192}, {"BLENDFACTOR", 193}, {"SRGBWRITEENABLE", 194},
    {"DEPTHBIAS", 195}, {"SEPARATEALPHABLENDENABLE", 206}, {"SRCBLENDALPHA", 207}, {"DESTBLENDALPHA", 208},
    {"BLENDOPALPHA", 209}, {0, 0}};
static const V6nName V6N_SAMP[] = {
    {"ADDRESSU", 1}, {"ADDRESSV", 2}, {"ADDRESSW", 3}, {"BORDERCOLOR", 4}, {"MAGFILTER", 5}, {"MINFILTER", 6},
    {"MIPFILTER", 7}, {"MIPMAPLODBIAS", 8}, {"MAXMIPLEVEL", 9}, {"MAXANISOTROPY", 10}, {"SRGBTEXTURE", 11}, {0, 0}};
static const V6nName V6N_TSS[] = {
    {"COLOROP", 1}, {"COLORARG1", 2}, {"COLORARG2", 3}, {"ALPHAOP", 4}, {"ALPHAARG1", 5}, {"ALPHAARG2", 6},
    {"TEXCOORDINDEX", 11}, {"TEXTURETRANSFORMFLAGS", 24}, {"COLORARG0", 26}, {"ALPHAARG0", 27}, {"RESULTARG", 28},
    {"CONSTANT", 32}, {0, 0}};
static const V6nName V6N_PRIM[] = {
    {"POINTLIST", 1}, {"LINELIST", 2}, {"LINESTRIP", 3}, {"TRIANGLELIST", 4}, {"TRIANGLESTRIP", 5},
    {"TRIANGLEFAN", 6}, {0, 0}};
static const V6nName V6N_VALUE[] = {
    {"FALSE", 0}, {"TRUE", 1},
    {"CMP_NEVER", 1}, {"CMP_LESS", 2}, {"CMP_EQUAL", 3}, {"CMP_LESSEQUAL", 4}, {"CMP_GREATER", 5},
    {"CMP_NOTEQUAL", 6}, {"CMP_GREATEREQUAL", 7}, {"CMP_ALWAYS", 8},
    {"BLEND_ZERO", 1}, {"BLEND_ONE", 2}, {"BLEND_SRCCOLOR", 3}, {"BLEND_INVSRCCOLOR", 4}, {"BLEND_SRCALPHA", 5},
    {"BLEND_INVSRCALPHA", 6}, {"BLEND_DESTALPHA", 7}, {"BLEND_INVDESTALPHA", 8}, {"BLEND_DESTCOLOR", 9},
    {"BLEND_INVDESTCOLOR", 10}, {"BLEND_SRCALPHASAT", 11}, {"BLEND_BOTHSRCALPHA", 12},
    {"BLEND_BOTHINVSRCALPHA", 13}, {"BLEND_BLENDFACTOR", 14}, {"BLEND_INVBLENDFACTOR", 15},
    {"BLENDOP_ADD", 1}, {"BLENDOP_SUBTRACT", 2}, {"BLENDOP_REVSUBTRACT", 3}, {"BLENDOP_MIN", 4}, {"BLENDOP_MAX", 5},
    {"CULL_NONE", 1}, {"CULL_CW", 2}, {"CULL_CCW", 3}, {"SHADE_FLAT", 1}, {"SHADE_GOURAUD", 2},
    {"FILL_POINT", 1}, {"FILL_WIREFRAME", 2}, {"FILL_SOLID", 3},
    {"STENCILOP_KEEP", 1}, {"STENCILOP_ZERO", 2}, {"STENCILOP_REPLACE", 3}, {"STENCILOP_INCRSAT", 4},
    {"STENCILOP_DECRSAT", 5}, {"STENCILOP_INVERT", 6}, {"STENCILOP_INCR", 7}, {"STENCILOP_DECR", 8},
    {"FOG_NONE", 0}, {"FOG_EXP", 1}, {"FOG_EXP2", 2}, {"FOG_LINEAR", 3},
    {"TADDRESS_WRAP", 1}, {"TADDRESS_MIRROR", 2}, {"TADDRESS_CLAMP", 3}, {"TADDRESS_BORDER", 4},
    {"TADDRESS_MIRRORONCE", 5}, {"TEXF_NONE", 0}, {"TEXF_POINT", 1}, {"TEXF_LINEAR", 2}, {"TEXF_ANISOTROPIC", 3},
    {"TOP_DISABLE", 1}, {"TOP_SELECTARG1", 2}, {"TOP_SELECTARG2", 3}, {"TOP_MODULATE", 4}, {"TOP_MODULATE2X", 5},
    {"TOP_MODULATE4X", 6}, {"TOP_ADD", 7}, {"TOP_ADDSIGNED", 8}, {"TOP_ADDSIGNED2X", 9}, {"TOP_SUBTRACT", 10},
    {"TOP_ADDSMOOTH", 11}, {"TOP_BLENDDIFFUSEALPHA", 12}, {"TOP_BLENDTEXTUREALPHA", 13},
    {"TOP_BLENDFACTORALPHA", 14}, {"TOP_BLENDCURRENTALPHA", 16}, {"TOP_DOTPRODUCT3", 24}, {"TOP_MULTIPLYADD", 25},
    {"TOP_LERP", 26},
    {"TA_DIFFUSE", 0}, {"TA_CURRENT", 1}, {"TA_TEXTURE", 2}, {"TA_TFACTOR", 3}, {"TA_SPECULAR", 4}, {"TA_TEMP", 5},
    {"TA_CONSTANT", 6}, {"TA_COMPLEMENT", 0x10}, {"TA_ALPHAREPLICATE", 0x20},
    {"TA_TEXTURE_COMPLEMENT", 0x12}, {"TA_DIFFUSE_COMPLEMENT", 0x10}, {"TA_TEXTURE_ALPHAREPLICATE", 0x22},
    {"TA_DIFFUSE_ALPHAREPLICATE", 0x20}, {"TA_CURRENT_ALPHAREPLICATE", 0x21},
    {0, 0}};

/* formats: D3DFORMAT value, bytes per texel (or per 4x4 block), block flag, data kind for the generator
 * ('b' bytes, 'h' half floats, 'f' floats) */
typedef struct V6nFmt { uint32_t value; int bytes, block; char kind; } V6nFmt;
static const V6nName V6N_FMT[] = {
    {"A8R8G8B8", 21}, {"X8R8G8B8", 22}, {"R5G6B5", 23}, {"X1R5G5B5", 24}, {"A1R5G5B5", 25}, {"A4R4G4B4", 26},
    {"A8", 28}, {"X4R4G4B4", 30}, {"A2B10G10R10", 31}, {"A8B8G8R8", 32}, {"X8B8G8R8", 33}, {"G16R16", 34},
    {"A2R10G10B10", 35}, {"A16B16G16R16", 36}, {"L8", 50}, {"A8L8", 51}, {"V8U8", 60}, {"Q8W8V8U8", 63},
    {"V16U16", 64}, {"L16", 81}, {"R16F", 111}, {"G16R16F", 112}, {"A16B16G16R16F", 113}, {"R32F", 114},
    {"G32R32F", 115}, {"A32B32G32R32F", 116},
    {"DXT1", FCC('D', 'X', 'T', '1')}, {"DXT2", FCC('D', 'X', 'T', '2')}, {"DXT3", FCC('D', 'X', 'T', '3')},
    {"DXT4", FCC('D', 'X', 'T', '4')}, {"DXT5", FCC('D', 'X', 'T', '5')}, {"ATI1", FCC('A', 'T', 'I', '1')},
    {"ATI2", FCC('A', 'T', 'I', '2')},
    {"D16_LOCKABLE", 70}, {"D32", 71}, {"D15S1", 73}, {"D24S8", 75}, {"D24X8", 77}, {"D24X4S4", 79}, {"D16", 80},
    {"D32F_LOCKABLE", 82}, {"D24FS8", 83}, {0, 0}};
static const V6nFmt V6N_FMTINFO[] = {
    {21, 4, 0, 'b'}, {22, 4, 0, 'b'}, {23, 2, 0, 'b'}, {24, 2, 0, 'b'}, {25, 2, 0, 'b'}, {26, 2, 0, 'b'},
    {28, 1, 0, 'b'}, {30, 2, 0, 'b'}, {31, 4, 0, 'b'}, {32, 4, 0, 'b'}, {33, 4, 0, 'b'}, {34, 4, 0, 'b'},
    {35, 4, 0, 'b'}, {36, 8, 0, 'b'}, {50, 1, 0, 'b'}, {51, 2, 0, 'b'}, {60, 2, 0, 'b'}, {63, 4, 0, 'b'},
    {64, 4, 0, 'b'}, {81, 2, 0, 'b'}, {111, 2, 0, 'h'}, {112, 4, 0, 'h'}, {113, 8, 0, 'h'}, {114, 4, 0, 'f'},
    {115, 8, 0, 'f'}, {116, 16, 0, 'f'},
    {FCC('D', 'X', 'T', '1'), 8, 1, 'b'}, {FCC('D', 'X', 'T', '2'), 16, 1, 'b'}, {FCC('D', 'X', 'T', '3'), 16, 1, 'b'},
    {FCC('D', 'X', 'T', '4'), 16, 1, 'b'}, {FCC('D', 'X', 'T', '5'), 16, 1, 'b'}, {FCC('A', 'T', 'I', '1'), 8, 1, 'b'},
    {FCC('A', 'T', 'I', '2'), 16, 1, 'b'}, {0, 0, 0, 0}};
static inline const V6nFmt* v6n_fmt(uint32_t v) {
    for (const V6nFmt* f = V6N_FMTINFO; f->value; f++) if (f->value == v) return f;
    return 0;
}
#endif
