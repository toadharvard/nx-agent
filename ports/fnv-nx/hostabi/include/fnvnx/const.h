#ifndef FNVNX_CONST_H
#define FNVNX_CONST_H
/* The declared constant of a body, as a record in the object, not as a call (the stub gate, verify/objstub.py, reads the records back; contract/l2consts.py is the format).
 *
 *   FNVNX_CONST(fn, type, value)   the function FN returns `value`, an object of TYPE (a record in `.fnvnx.const`: pointer to FN, u32 sizeof(type), the value bytes)
 *   FNVNX_CONST_VOID(fn)           the function FN returns nothing (the same record with length 0)
 *   FNVNX_CONST_IDENT(fn, k)       the function FN returns its argument k (0-based) unchanged (a record in `.fnvnx.ident`: pointer to FN, u32 k)
 *
 * FN is an EXPRESSION whose value is the address of the function: a function name, `&name`, or for a C++ member GCC's pmf-to-function conversion
 *   (void *)(RET (*)([const] C *, PARAMS))&C::m
 * (virtual overrides and const members included; the cast is written by the caller, the -Wpmf-conversions it triggers is silenced inside the macro). The compiler
 * emits the symbol, so a mangled name is never written by hand. The pointer field is a relocation (R_AARCH64_P32_ABS32 in an ILP32 guest object, R_X86_64_64 in a host
 * object); value is a constant expression of TYPE. The record is a static const object in a section with flags "" (no SHF_ALLOC): no byte reaches the loaded image, and a reference
 * from it keeps nothing alive under --gc-sections. Each record is aligned to 4 and padded to 4. A static_assert ties the record to its length: sizeof(record) is the
 * pointer, the u32 and sizeof(type) bytes, rounded up to 4. Works in C (GNU C, C11 _Static_assert) and in C++. FN must be declared before the macro. */
#ifdef __cplusplus
#define FNVNX_CONST_SA(c, m) static_assert(c, m)
#define FNVNX_CONST_OPEN _Pragma("GCC diagnostic push") _Pragma("GCC diagnostic ignored \"-Wpmf-conversions\"")
#define FNVNX_CONST_CLOSE _Pragma("GCC diagnostic pop")
#else
#define FNVNX_CONST_SA(c, m) _Static_assert(c, m)
#define FNVNX_CONST_OPEN
#define FNVNX_CONST_CLOSE
#endif
#define FNVNX_CONST_CAT2(a, b) a##b
#define FNVNX_CONST_CAT(a, b) FNVNX_CONST_CAT2(a, b)

#define FNVNX_CONST_REC(n, sec, fields, size, ...)                                                                                      \
    FNVNX_CONST_OPEN                                                                                                                      \
    __attribute__((section(sec ",\"\",%progbits\n#"), used, aligned(4))) static const struct __attribute__((packed, aligned(4))) { fields } \
        FNVNX_CONST_CAT(fnvnx_const_rec_, n) = { __VA_ARGS__ };                                                                           \
    FNVNX_CONST_SA(sizeof(FNVNX_CONST_CAT(fnvnx_const_rec_, n)) == (sizeof(void *) + 4 + (size) + 3) / 4 * 4,                            \
                   "fnvnx const record: the pointer, the u32 and the value, padded to 4");                                               \
    FNVNX_CONST_CLOSE

#define FNVNX_CONST_N(n, fn, type, value) \
    FNVNX_CONST_REC(n, ".fnvnx.const", void *f; unsigned len; type v;, sizeof(type), (void *)(fn), (unsigned)sizeof(type), (value))
#define FNVNX_CONST(fn, type, value) FNVNX_CONST_N(__COUNTER__, fn, type, value)

#define FNVNX_CONST_VOID_N(n, fn) FNVNX_CONST_REC(n, ".fnvnx.const", void *f; unsigned len;, 0, (void *)(fn), 0u)
#define FNVNX_CONST_VOID(fn) FNVNX_CONST_VOID_N(__COUNTER__, fn)

#define FNVNX_CONST_IDENT_N(n, fn, argk) FNVNX_CONST_REC(n, ".fnvnx.ident", void *f; unsigned idx;, 0, (void *)(fn), (unsigned)(argk))
#define FNVNX_CONST_IDENT(fn, k) FNVNX_CONST_IDENT_N(__COUNTER__, fn, k)
#endif
