/*
 * shcache.h - the shader translation cache: a pre-filled MEMO of fnvnx_shader_translate (render/shaders/translator/include/fnvnx_shaders.h).
 * Portable C11, libc only (malloc), no I/O: the host hands the cache file and the append log in as memory and receives log records
 * through a sink. Builds for LP64 and ILP32 (every on-disk field is read byte-wise, little-endian; no struct is cast).
 *
 * Contract (the proof obligation): an entry for key K holds EXACTLY the bytes the run-time translator produces for K
 * (flags and link set taken from K). A miss, and a cache that fails any check below, mean "translate now": that is a complete path,
 * never a failure. The cache never decides what a shader is; it can only return what the translator returned before.
 *
 * KEY = (bytecode hash = fnvnx_shader_hash, bytecode length, stage, variant flags, L).
 *   variant flags: the bits of fnvnx_shader_opts.flags that change the output (EPI_STATIC/ALPHA/FOG, NUMERICS_NINE); KEEP_GLSL / NO_DKSH
 *     are not part of the key: the cache holds complete translations (DKSH + reflection).
 *   L: the canonical varying link set (shcache_link_set) when the stage's locations depend on the draw's VS/PS pair, else empty: a
 *     stage-alone translation (link_pairs = NULL). The run-time caller passes L (sorted ascending, unique) as link_pairs unchanged.
 *   The 64-bit hash is the translator's own cache key; the stored length and the stored L (compared byte-wise) narrow it, and the builder
 *   refuses two different bytecodes with one hash (d9s_shaders.py compares SHA-256), so no shipped key is ambiguous.
 *
 * FILE (little-endian):  header 64 bytes: "FNSC", u32 version(1), u8 build_id[16], u32 entry_count, u32 index_off(64), u32 blob_off,
 *   u32 file_size, u64 body_hash (FNV-1a 64 of bytes [64, file_size)), 16 zero bytes.  index: entry_count x 36 bytes, strictly increasing by
 *   (code_hash, stage, flags, l_hash, code_len): u64 code_hash, u64 l_hash, u32 code_len, u32 flags, u8 stage, u8 n_l, u8 0, u8 0, u32 blob_off,
 *   u32 blob_size.  blob: u8 n_l, u8 l[n_l], zero pad to 4, then the payload.  Entries are laid out in index order, back to back.
 * LOG (append only): header "FNSL", u32 version, u8 build_id[16] (24 bytes); records u32 len (whole record), u64 fnv1a64(len bytes ++ body),
 *   body = u64 code_hash, u32 code_len, u32 flags, u8 stage, u8 n_l, u8 l[n_l], payload.  A record that is short, longer than the remaining
 *   bytes, fails its checksum or holds an illegal key ends the log: it and everything after it are ignored (torn write); shcache_info
 *   reports log_valid_len so the host can truncate before it appends again.
 * Any of: bad magic, other version, other build id, size mismatch, bad body_hash, index out of bounds or unsorted, nonzero reserved bytes
 *   makes the main file EMPTY.  A log with a bad header or build id is empty.
 */
#ifndef SHCACHE_H
#define SHCACHE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define SHCACHE_VERSION 1
#define SHCACHE_BUILD_ID_LEN 16
#define SHCACHE_MAX_L 31                 /* FNVNX_DK_VARYING_SLOTS */
#define SHCACHE_STAGE_VERTEX 0
#define SHCACHE_STAGE_PIXEL 1
#define SHCACHE_FLAGS_MASK ((1u << 2) | (1u << 3) | (1u << 4) | (1u << 5))   /* FNVNX_SHADER_EPI_STATIC|EPI_ALPHA|EPI_FOG|NUMERICS_NINE */
#define SHCACHE_MAIN_HEADER 64
#define SHCACHE_INDEX_ENTRY 36
#define SHCACHE_LOG_HEADER 24

typedef struct shcache_key {
    uint64_t code_hash;                  /* fnvnx_shader_hash(bytecode) = shcache_hash64 */
    uint32_t code_len;                   /* bytes of bytecode */
    uint32_t flags;                      /* masked by SHCACHE_FLAGS_MASK */
    uint8_t stage, n_l;
    uint8_t l[SHCACHE_MAX_L];            /* FNVNX_DK_PAIR values, ascending, unique; n_l = 0: stage alone */
} shcache_key;

typedef struct shcache shcache;
typedef struct shcache_info {
    int main_valid;                      /* 1: the main file was accepted (0: empty, whatever the reason) */
    uint32_t main_entries, log_entries;  /* log_entries: accepted records (not counting ones already in the main file) */
    size_t log_valid_len;                /* bytes of the log that are valid (0: no valid header); truncate the file to this before appending */
    uint32_t log_ignored;                /* records ignored because their key was already present */
} shcache_info;
typedef int (*shcache_sink)(void *user, const void *bytes, size_t n);   /* append n bytes to the log file; 0 = ok */

uint64_t shcache_hash64(const void *p, size_t n);                        /* FNV-1a 64: equals fnvnx_shader_hash */
uint64_t shcache_l_hash(const uint8_t *l, uint32_t n_l);                 /* 0 for the empty set, else a nonzero FNV-1a 64 of the pairs */
/* canonicalises l (sorts, rejects duplicates, usage > 13, index > 15, more than SHCACHE_MAX_L); flags are masked. 0 or -1. */
int shcache_key_make(shcache_key *k, uint64_t code_hash, uint32_t code_len, int stage, uint32_t flags, const uint8_t *l, uint32_t n_l);

/* The link set of a draw's pair, from the declared pairs (FNVNX_DK_PAIR) of the VS outputs and the PS inputs (vPos/vFace not included).
 * *n_l = 0 when every pair of L is canonical (fnvnx_dk_varying_location >= 0): the stage-alone translations are valid. Else l holds L sorted
 * ascending (<= SHCACHE_MAX_L). Returns 0, or -1 if the union is not a legal declaration set (the translator rejects it too). */
int shcache_link_set(const uint8_t *vs_out, uint32_t n_vs, const uint8_t *ps_in, uint32_t n_ps, uint8_t *l, uint32_t *n_l);
/* declared pairs of a stage's reflection JSON (fnvnx_shader_result.reflection_json): section "inputs" or "outputs"; entries whose usage is
 * not a D3D9 usage (vPos, vFace) and, for a PS, oC colour outputs are not interpolants and are skipped. Returns the count or -1 (malformed). */
int shcache_refl_pairs(const char *json, const char *section, uint8_t *pairs, uint32_t cap);
/* shcache_link_set over the two reflections: vs_json's "outputs", ps_json's "inputs". */
int shcache_link_key(const char *vs_json, const char *ps_json, uint8_t *l, uint32_t *n_l);

/* standard payload: u32 dksh_size, u32 refl_size, dksh, reflection json (no NUL). pack returns bytes written (0 if cap too small). */
size_t shcache_payload_pack(const void *dksh, uint32_t dn, const char *json, uint32_t jn, uint8_t *out, size_t cap);
int shcache_payload_unpack(const void *p, size_t n, const uint8_t **dksh, uint32_t *dn, const char **json, uint32_t *jn);

/* open: main/log may be NULL/0. build_id: the translator build the host runs (NULL = accept any). Never returns a failed cache for bad
 * data (it is empty); NULL only if memory is exhausted. The buffers must outlive the cache. */
shcache *shcache_open(const void *main_bytes, size_t main_n, const void *log_bytes, size_t log_n, const uint8_t *build_id);
void shcache_close(shcache *c);
void shcache_get_info(const shcache *c, shcache_info *info);
int shcache_lookup(const shcache *c, const shcache_key *k, const void **payload, size_t *n);   /* 1 hit, 0 miss */
/* adds the entry (payload copied) and hands ONE buffer to sink: the record, preceded by the 24-byte log header when the log has no valid
 * header yet (log_valid_len == 0), so a fresh log is never headerless. The host truncates its log file to info.log_valid_len at open and
 * appends what the sink gets. Returns 1 stored, 0 key already present, -1 out of memory or sink failure (nothing stored). */
int shcache_put(shcache *c, const shcache_key *k, const void *payload, size_t n, shcache_sink sink, void *user);
/* on-disk encodings, shared with the builder tool's tests */
size_t shcache_log_record(const shcache_key *k, const void *payload, size_t n, uint8_t *out, size_t cap);   /* bytes written, 0 if cap too small */

#ifdef __cplusplus
}
#endif
#endif
