// V6n claim check C77 (NxOp_Marker PASS tags). Random frames of passes and packets, some tagged with
// (engine pass id, family) via nx::Frame::Tag, some untagged, some clears, are encoded by nx::Frame::Finish through a
// CaptureBackend (no inner backend). Every frame's stream is parsed independently and checked against the rule of
// gpu_abi.h / RENDER_NATIVE.md §6.1:
//   - each draw is preceded (within its pass) by a marker carrying its own tag, or by none when it is untagged and
//     no tagged draw came before it in the pass; an untagged draw after a tagged one sits under (NX_PASS_UNKNOWN, 0);
//   - a marker is emitted only when the tag changes: no two markers in a row without a draw between, and never the
//     same tag twice in a row (one marker per run of equally tagged draws, after sorting);
//   - a pass with no tagged draw has no marker; markers never cross BeginPass/EndPass;
//   - the capture file holds the encoded words verbatim (markers survive capture).
// Draws are identified by Packet.first (unique per frame). Exit 1 on the first violation.
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <random>
#include "nx/front.h"
#include "capture/capture.h"
#include "nx/families.h"

static int fail(const char* what, int it, int pass) { std::printf("C77 FAIL case %d pass %d: %s\n", it, pass, what); return 1; }

int main(int argc, char** argv) {
    const char* capPath = argc > 1 ? argv[1] : "/tmp/v6n_c77.nxcap";
    FILE* f = std::fopen(capPath, "wb");
    if (!f) { std::printf("cannot write %s\n", capPath); return 2; }
    nx::CaptureBackend cap;
    if (!cap.Open(nullptr, f)) { std::printf("capture open failed\n"); return 2; }
    nx::Frame fr;
    if (!fr.Init(&cap, 1u << 20, 256)) { std::printf("frame init failed\n"); return 2; }
    std::mt19937 rng(77);
    const uint16_t ids[] = {0, 1, 2, 568, 569, 671, 757, 758};   // incl. 0 (disabled) and header-number 671
    std::vector<std::vector<uint32_t>> streams;
    long draws = 0, markers = 0, untaggedPasses = 0;
    nx::StateCache sc;
    for (int it = 0; it < 3000; it++) {
        fr.Begin((uint32_t)it + 1);
        int np = 1 + (int)(rng() % 4);
        std::vector<int> passMode(np);
        for (int p = 0; p < np; p++) {
            nx::PassDesc pd; std::memset(&pd, 0, sizeof pd); pd.width = 64; pd.height = 64;
            fr.AddPass(pd);
            passMode[p] = (int)(rng() % 3);   // 0 untagged, 1 all tagged, 2 mixed
        }
        // expected tag per draw id (Packet.first): 0 = untagged, else pass | family << 16
        std::vector<uint32_t> want;
        int n = (int)(rng() % 40);
        for (int k = 0; k < n; k++) {
            int p = (int)(rng() % np);
            if (rng() % 8 == 0) {   // a clear packet (never marked)
                fr.Clear((uint8_t)p, (uint8_t)(rng() % 3), (uint16_t)(rng() % 50), 1, 0, 1.0f, 0, 0, 0, 8, 8);
                continue;
            }
            nx::Packet* pk = fr.Emit();
            if (!pk) break;
            uint32_t id = (uint32_t)want.size() + 1000;
            pk->first = id; pk->count = 3; pk->prim = NxPrim_Triangles;
            pk->state = (uint16_t)(1 + rng() % 4); pk->vtxBlock = 1; pk->vtxOffset = 0; pk->vtxSize = 64;
            pk->key = nx::SortKey((uint8_t)p, (uint8_t)(rng() % 3), pk->state, (uint16_t)(rng() % 3), (uint16_t)(rng() % 50));
            uint32_t tag = 0;
            if (passMode[p] == 1 || (passMode[p] == 2 && rng() % 2)) {
                uint16_t pid = ids[rng() % 8]; uint16_t fam = (uint16_t)(1 + rng() % 7);
                if (rng() % 3 == 0) { pid = 757; fam = NxFam_Tile; }   // long runs of one tag
                fr.Tag(pk, pid, fam); tag = (uint32_t)pid | (uint32_t)fam << 16;
            } else if (rng() % 4 == 0) {
                fr.Tag(pk, ids[rng() % 8], 0);   // family 0 = untagged, whatever the pass id
            }
            want.push_back(tag);
        }
        fr.Finish(sc, nullptr);
        const uint32_t* w = fr.encodedData(); uint32_t nw = fr.encodedWords();
        streams.emplace_back(w, w + nw);
        // parse
        int pass = -1; bool inPass = false, haveMarker = false, drawSinceMarker = true, anyTagged = false, anyMarker = false;
        uint32_t cur = 0, lastMarker = ~0u;
        std::vector<int> seen(want.size(), 0);
        for (uint32_t i = 0; i < nw; ) {
            uint32_t op = w[i] & 0xFFFF, ac = w[i] >> 16; const uint32_t* a = w + i + 1; i += 1 + ac;
            if (op == NxOp_BeginPass) { inPass = true; pass++; haveMarker = false; drawSinceMarker = true; cur = 0; lastMarker = ~0u; anyTagged = anyMarker = false; }
            else if (op == NxOp_EndPass) {
                if (anyMarker && !anyTagged) return fail("marker in a pass without tagged draws", it, pass);
                if (!anyTagged) untaggedPasses++;
                inPass = false;
            } else if (op == NxOp_Marker) {
                if (!inPass) return fail("marker outside a pass", it, pass);
                if (ac != 3 || a[0] != NX_MARKER_PASS) return fail("marker shape", it, pass);
                if (!drawSinceMarker) return fail("two markers without a draw between", it, pass);
                uint32_t t = a[1] == NX_PASS_UNKNOWN ? 0u : (a[1] | a[2] << 16);
                if (a[1] == NX_PASS_UNKNOWN && a[2] != 0) return fail("UNKNOWN marker with a family", it, pass);
                if (a[1] != NX_PASS_UNKNOWN && (a[1] > 758 || a[2] == 0 || a[2] > 7)) return fail("marker pass id/family out of range", it, pass);
                if (t == lastMarker) return fail("same tag marked twice in a row", it, pass);
                if (!haveMarker && t == 0) return fail("UNKNOWN marker before any tagged draw", it, pass);
                lastMarker = t; cur = t; haveMarker = true; drawSinceMarker = false; anyMarker = true; markers++;
            } else if (op == NxOp_Draw || op == NxOp_DrawIndexed) {
                uint32_t id = a[3] - 1000;
                if (id >= want.size() || seen[id]++) return fail("unknown or repeated draw", it, pass);
                if (want[id] != cur) {
                    std::printf("draw %u want %08x under %08x\n", id, want[id], cur);
                    return fail("draw not under its own tag", it, pass);
                }
                if (want[id]) anyTagged = true;
                drawSinceMarker = true; draws++;
            }
        }
        for (size_t k = 0; k < want.size(); k++) if (!seen[k]) return fail("draw missing from the stream", it, -1);
    }
    cap.Close();
    // the capture holds every frame's words verbatim
    FILE* g = std::fopen(capPath, "rb");
    std::vector<uint8_t> d; { uint8_t b[65536]; size_t r; while ((r = std::fread(b, 1, sizeof b, g)) > 0) d.insert(d.end(), b, b + r); }
    std::fclose(g);
    size_t off = 8, s = 0;
    while (off + 8 <= d.size()) {
        uint32_t tag, n; std::memcpy(&tag, &d[off], 4); std::memcpy(&n, &d[off + 4], 4);
        if (tag == nx::kCapSubmit) {
            uint32_t words; std::memcpy(&words, &d[off + 8 + 8], 4);
            if (s >= streams.size() || words != streams[s].size() || std::memcmp(&d[off + 8 + 12], streams[s].data(), words * 4))
                return fail("capture words differ from the encoded stream", (int)s, -1);
            s++;
        }
        off += 8 + ((n + 3) & ~3u);
    }
    if (s != streams.size()) return fail("capture frame count", (int)s, -1);
    std::printf("C77 markers: 3000 frames, %ld draws, %ld markers, %ld untagged passes, capture verbatim (%zu frames): PASS\n",
                draws, markers, untaggedPasses, s);
    return 0;
}
