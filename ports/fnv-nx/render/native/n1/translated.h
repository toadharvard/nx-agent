// fnv-nx native renderer: the translated path (RENDER_NATIVE.md §4.5, nx::Family::Translated).
// The game's own VS/PS of a pass, translated by S6b's sdp2dksh at install/build time from the player's shader
// packages and packed into translated.nxsp (render/native/tools/nxsp_pack.py), which the console loads at run time
// from sdmc:/switch/fnv/translated.nxsp (FNVNX_SD_TRANSLATED; game-derived, never in the RomFS): every program as precompiled DKSH, indexed by its bytecode hash (fnvnx_shader_hash).
// No shader is compiled on the console; a program without DKSH is a skipped draw, never a compile. Host / emulator
// packs (--glsl) also carry each program's GLSL for the GL reference backend. Game-derived: never in the repository.
// Constants follow the translator's layout contract (render/shaders/include/fnvnx_dk_layout.h): per stage UBO 0 =
// c[256], UBO 1 = i[16] + b[16] (bools at byte 256 + 16n), UBO 2 = driver constants; sampler binding = register.
//
// Container: NX1's NXSP v1 (toolchain/nro/shaderpack.py, shaders.nxsp):
//   header  "NXSP", u32 version 1, u32 count, u32 index offset, u32 data offset, u8[12] pack id      (32 bytes)
//   index   count x { char name[48], family[24], variant[16] (NUL-padded); u32 stage (0 vert, 1 frag), offset, size,
//                     crc32 of the blob }                                                            (104 bytes)
//   data    blobs at 256-byte boundaries
// Rows of a translated pack:
//   family "translated"       name = 16 lowercase hex digits of the hash, variant = "<vs|ps>_<major>_<minor>/<mask>"
//                             (mask: 4 hex digits, the VS input locations read), blob = DKSH
//   family "translated-glsl"  (host / emulator packs only) the same name, blob = the GLSL text, NUL-terminated
//   family "translated-name"  name = the game's program name (e.g. "SLS2001.vso"), variant = the hash's 16 hex
//                             digits, size 0 (the engine's program objects carry names; the hooked device, hashes)
#ifndef FNVNX_NX_TRANSLATED_H
#define FNVNX_NX_TRANSLATED_H
#include <stdint.h>

namespace nx {

struct TranslatedProgram {
    uint64_t hash;
    uint8_t stage, major, minor, pad;
    uint16_t inputMask, pad2;
    const uint8_t* dksh; uint32_t dkshSize;   // 0 / 0: no precompiled DKSH (the draw is skipped)
    const char* glsl; uint32_t glslSize;      // 0 / 0 in a console pack
};

class TranslatedStore {
public:
    // data stays owned by the caller and must outlive the store (the guest reads the file into memory once).
    bool Load(const void* data, uint32_t size);
    void Clear();
    ~TranslatedStore() { Clear(); }
    uint32_t count() const { return n_; }
    int Find(uint64_t hash) const;                          // -1: not in the pack
    int FindName(const char* name) const;                   // by the game's program name
    const TranslatedProgram& program(int i) const { return prog_[i]; }
    static uint64_t Hash(const void* bytecode, uint32_t bytes);   // FNV-1a 64 (fnvnx_shader_hash)
    static uint32_t Crc32(const void* data, uint32_t bytes);     // zlib's crc32 (the NXSP row checksum)
    static uint32_t BytecodeBytes(const uint32_t* tokens, uint32_t maxTokens);   // up to and incl. the END token
private:
    TranslatedProgram* prog_ = nullptr;     // sorted by hash
    const char** names_ = nullptr;          // name rows: name, program index
    int32_t* nameProg_ = nullptr;
    uint32_t n_ = 0, nNames_ = 0;
};

// The register files of one translated draw (what the engine wrote, in the translator's layout).
struct TranslatedConstants {
    float vsc[256][4], psc[256][4];
    int32_t vsi[16][4], psi[16][4];
    uint32_t vsb[16], psb[16];
};

} // namespace nx
#endif
