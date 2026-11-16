/* FNVNX_EXPORT(module, name, symbol, kind): one record of the section `.fnvnx.export` (contract/exportgen.py reads it into exports.tsv).
 * It says: the function or data object `symbol`, defined in this translation unit, is what the import `name` of the declared-machine module `module`
 * (a lower-case file name, e.g. "msvcrt.dll") resolves to.  `kind` is `func` or `data`.  The record is placed beside the definition and is the only
 * place that fact is written: the bridge table (contract/bridgetable.py) reads exports.tsv and no hand list names a symbol for an import.
 * Text, LF-terminated, TAB-separated, not loaded. */
#ifndef FNVNX_EXPORT_H
#define FNVNX_EXPORT_H
#if defined(__aarch64__) || defined(__arm__)
#define FNVNX_PROGBITS "%progbits"   /* the assembler's section-type spelling on Arm */
#else
#define FNVNX_PROGBITS "@progbits"
#endif
#define FNVNX_EXPORT(module, name, symbol, kind) \
    __asm__(".pushsection .fnvnx.export,\"\"," FNVNX_PROGBITS "\n\t.ascii \"" module "\\t" name "\\t" #symbol "\\t" kind "\\n\"\n\t.popsection")
#endif
