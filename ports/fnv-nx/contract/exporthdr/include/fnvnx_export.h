/* FNVNX_EXPORT(module, name, symbol, kind): one record of the section `.fnvnx.export` (contract/exportgen.py reads it into exports.tsv).
 * It says: the function or data object `symbol`, defined in this translation unit, is what the import `name` of the declared-machine module `module`
 * (a lower-case file name, e.g. "msvcrt.dll") resolves to.  `kind` is `func` (a native function: the bridge marshals it by the type of its definer), `entry` (a body that works on the
 * guest state itself: the bridge jumps to it and performs the whole x86 return, nothing is marshalled; its definer is `void(void)`) or `data`.  `module`, `name` and `kind` are string literals.
 * The record is placed AFTER the declaration of the definer and is the only place that fact is written: the bridge table (contract/bridgetable.py) reads exports.tsv and no hand list names a symbol
 * for an import.  The definer is referenced as `&symbol`, so the compiler resolves it (a name that is not declared is a compile error) and the object carries a relocation against that symbol, which is how
 * exportgen names it: the symbol is never text.  Record layout: the three strings NUL-terminated, then a pointer-sized field holding `&symbol`, then zero padding to the next record.
 * Not loaded (the section's flags are empty). */
#ifndef FNVNX_EXPORT_H
#define FNVNX_EXPORT_H
#if defined(__aarch64__) || defined(__arm__)
#define FNVNX_PROGBITS "%progbits"   /* the assembler's section-type spelling on Arm */
#else
#define FNVNX_PROGBITS "@progbits"
#endif
#define FNVNX_XCAT2(a, b) a##b
#define FNVNX_XCAT(a, b) FNVNX_XCAT2(a, b)
/* The section attribute carries the section's own directive tail: flags "" (not alloc) and the type; the `#` after the newline comments out the flags GCC appends. */
#define FNVNX_EXPORT(module, name, symbol, kind) \
    static const struct { char m[sizeof(module)]; char n[sizeof(name)]; char k[sizeof(kind)]; const void *s; } \
        FNVNX_XCAT(fnvnx_export_rec_, __COUNTER__) __attribute__((used, section(".fnvnx.export,\"\"," FNVNX_PROGBITS "\n#"))) = {module, name, kind, (const void *)&symbol}
#endif
