/* ilp32-wrap: 32-bit address arithmetic on an integer-constant base address (synthetic, S13b).
 *
 * The exe (MSVC, 32-bit) adds a base address and an index modulo 2^32. The decomp writes exe globals as integer
 * constants (*(char *)(0x011f6370 + i)). With such a constant base, GCC's AArch64 ILP32 code at -O1 and above
 * folds the 32-bit sum into a 64-bit address (add x, base, w, uxtw), so an index of -1 reaches 2^32 + base - 1
 * and faults where the exe reads base[-1]. This holds even for a signed int index, which is well-defined C.
 * Measured with Ubuntu GCC 13.3 (-mabi=ilp32 -S):
 *   - -fwrapv-pointer changes nothing;
 *   - -fno-tree-ter fixes by_unsigned and by_int but not by_pointer;
 *   - a symbol base (extern array, PIE or not) is correct.
 * check.sh runs it on the port's own compiler. */
#define TABLE 0x011f6370u           /* an absolute image address, as the decomp writes them */

unsigned char by_unsigned(unsigned i) { return *(unsigned char *)(TABLE + i); }   /* i = 0xffffffff -> TABLE - 1 */
unsigned char by_pointer(char *p) { return *(p + TABLE); }                           /* p = (char *)-1 -> TABLE - 1 */
unsigned char by_int(int i) { return ((unsigned char *)TABLE)[i]; }                  /* control: sign-extended, ok */
