/* REF1: the C front end's only source of a reference type is the builtin va_list prototypes (c-family/c-common.cc:4709, BT_VALIST_REF).
 * A user declaration of such a builtin composes with the builtin's type or replaces it; the decl is no longer an undeclared builtin. */
extern void __builtin_va_start(__builtin_va_list, ...);
extern void __builtin_va_end(__builtin_va_list);
extern void __builtin_va_copy(__builtin_va_list, __builtin_va_list);
void use(__builtin_va_list a, __builtin_va_list b) { __builtin_va_copy(a, b); __builtin_va_end(a); }
