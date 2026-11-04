// fnv-nx native renderer: the translated program pack (translated.nxsp, NX1's NXSP container). See translated.h.
#include "translated.h"
#include <new>
#include <string.h>

namespace nx {

static inline uint32_t rd(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }

uint32_t TranslatedStore::Crc32(const void* data, uint32_t n) {
    const uint8_t* p = (const uint8_t*)data;
    static uint32_t t[256];
    static bool init = false;
    if (!init) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            t[i] = c;
        }
        init = true;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < n; i++) c = t[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

static bool Hex16(const char* s, uint64_t* out) {   // exactly 16 lowercase hex digits
    uint64_t v = 0;
    for (int i = 0; i < 16; i++) {
        const char c = s[i];
        const int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
        if (d < 0) return false;
        v = v << 4 | (uint64_t)d;
    }
    *out = v;
    return true;
}

void TranslatedStore::Clear() {
    delete[] prog_; delete[] names_; delete[] nameProg_;
    prog_ = nullptr; names_ = nullptr; nameProg_ = nullptr; n_ = nNames_ = 0;
}

bool TranslatedStore::Load(const void* data, uint32_t size) {
    Clear();
    const uint8_t* b = (const uint8_t*)data;
    if (!b || size < 32 || memcmp(b, "NXSP", 4) || rd(b + 4) != 1) return false;
    const uint32_t n = rd(b + 8), idx = rd(b + 12);
    if (idx > size || (uint64_t)n * 104 > size - idx) return false;
    // validate every row, count programs and names
    uint32_t np = 0, nn = 0;
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t* e = b + idx + 104ull * i;
        const uint32_t off = rd(e + 92), sz = rd(e + 96);
        if (e[47] || e[47 + 24] || (uint64_t)off + sz > size || (sz && (off % 256 || TranslatedStore::Crc32(b + off, sz) != rd(e + 100))))
            return false;
        const char* fam = (const char*)e + 48;
        if (!strcmp(fam, "translated")) np++;
        else if (!strcmp(fam, "translated-name")) nn++;
    }
    prog_ = new (std::nothrow) TranslatedProgram[np ? np : 1];
    names_ = new (std::nothrow) const char*[nn ? nn : 1];
    nameProg_ = new (std::nothrow) int32_t[nn ? nn : 1];
    if (!prog_ || !names_ || !nameProg_) { Clear(); return false; }
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t* e = b + idx + 104ull * i;
        const char* fam = (const char*)e + 48;
        if (strcmp(fam, "translated")) continue;
        TranslatedProgram& p = prog_[n_];
        memset(&p, 0, sizeof p);
        char var[17];
        memcpy(var, e + 72, 16); var[16] = 0;
        unsigned maj = 0, mnr = 0, mask = 0;
        const char* v = var + 3;   // "vs_3_0/0051"
        if (!Hex16((const char*)e, &p.hash) || (var[0] != 'v' && var[0] != 'p')) { Clear(); return false; }
        maj = (unsigned)(v[0] - '0'); mnr = v[2] == 'x' ? 1 : (unsigned)(v[2] - '0');
        const char* m = strchr(var, '/');
        for (const char* q = m ? m + 1 : ""; *q; q++) mask = mask << 4 | (unsigned)(*q <= '9' ? *q - '0' : *q - 'a' + 10);
        p.stage = (uint8_t)rd(e + 88); p.major = (uint8_t)maj; p.minor = (uint8_t)mnr; p.inputMask = (uint16_t)mask;
        p.dksh = rd(e + 96) ? b + rd(e + 92) : nullptr; p.dkshSize = rd(e + 96);
        n_++;
    }
    // sort by hash (insertion: packs are written sorted, so this is one pass)
    for (uint32_t i = 1; i < n_; i++)
        for (uint32_t j = i; j > 0 && prog_[j - 1].hash > prog_[j].hash; j--) {
            TranslatedProgram t = prog_[j]; prog_[j] = prog_[j - 1]; prog_[j - 1] = t;
        }
    for (uint32_t i = 1; i < n_; i++) if (prog_[i].hash == prog_[i - 1].hash) { Clear(); return false; }
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t* e = b + idx + 104ull * i;
        const char* fam = (const char*)e + 48;
        uint64_t h;
        if (!strcmp(fam, "translated-glsl")) {
            const uint32_t off = rd(e + 92), sz = rd(e + 96);
            if (!Hex16((const char*)e, &h) || !sz || b[off + sz - 1]) { Clear(); return false; }
            const int k = Find(h);
            if (k >= 0) { prog_[k].glsl = (const char*)b + off; prog_[k].glslSize = sz - 1; }
        } else if (!strcmp(fam, "translated-name")) {
            char var[17];
            memcpy(var, e + 72, 16); var[16] = 0;
            if (!Hex16(var, &h)) { Clear(); return false; }
            names_[nNames_] = (const char*)e;
            nameProg_[nNames_++] = Find(h);
        }
    }
    return true;
}

int TranslatedStore::Find(uint64_t hash) const {
    int lo = 0, hi = (int)n_ - 1;
    while (lo <= hi) {
        const int m = (lo + hi) / 2;
        if (prog_[m].hash == hash) return m;
        if (prog_[m].hash < hash) lo = m + 1; else hi = m - 1;
    }
    return -1;
}

int TranslatedStore::FindName(const char* name) const {
    if (!name) return -1;
    for (uint32_t i = 0; i < nNames_; i++) if (!strncmp(names_[i], name, 48)) return nameProg_[i];
    return -1;
}

uint64_t TranslatedStore::Hash(const void* bytecode, uint32_t n) {
    const uint8_t* p = (const uint8_t*)bytecode;
    uint64_t h = 0xcbf29ce484222325ull;
    for (uint32_t i = 0; i < n; i++) { h ^= p[i]; h *= 0x100000001b3ull; }
    return h;
}

// D3D9 shader token stream: version token, then instructions; comments (opcode 0xFFFE) carry their length in bits
// 16-30; SM2+ instructions carry theirs in bits 24-27; END = 0x0000FFFF.
uint32_t TranslatedStore::BytecodeBytes(const uint32_t* t, uint32_t maxTokens) {
    if (!t || !maxTokens) return 0;
    const uint32_t major = (t[0] >> 8) & 0xFF;
    uint32_t i = 1;
    while (i < maxTokens) {
        const uint32_t tok = t[i];
        if (tok == 0x0000FFFFu) return 4 * (i + 1);
        if ((tok & 0xFFFF) == 0xFFFE) { i += 1 + ((tok >> 16) & 0x7FFF); continue; }
        if (major >= 2) { i += 1 + ((tok >> 24) & 0xF); continue; }
        i++;   // SM1: parameters until the next token with bit 31 clear
        while (i < maxTokens && (t[i] & 0x80000000u)) i++;
    }
    return 0;
}

} // namespace nx
