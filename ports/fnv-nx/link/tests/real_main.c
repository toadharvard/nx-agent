/* TEST FIXTURE of link/tests/real_guest.py: the `main` of a guest linked from the real guest runtime and the synthetic lifted set.
 * Looks up two dispatcher keys through the lifted runtime's own `fnvl_dispatch` and checks what comes back:
 *   FIRST_ADDR  (the first universe address)  must give the address of its body FIRST_BODY (a symbol of lift_K_of_N.a), compared, not called
 *               (a synthetic body calls the next body, the whole ring: the set is a link-scale stand-in, not runnable code);
 *   IMPORT_KEY  (an import key of keys.tsv)   must give IMPORT_BODY, a leaf that counts its calls: it is called and the count read back.
 * Prints one line and returns 0 when both hold. */
#include <stdint.h>
#include <stdio.h>

extern void (*fnvl_dispatch(uint32_t a))(void);
extern void FIRST_BODY(void);
extern void IMPORT_BODY(void);
extern volatile unsigned fnvl_import_hits;

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    void (*first)(void) = fnvl_dispatch(FIRST_ADDR);
    void (*imp)(void) = fnvl_dispatch(IMPORT_KEY);
    int ok_first = first == FIRST_BODY, ok_imp = imp == IMPORT_BODY;
    if (ok_imp) imp();
    printf("link proof: dispatch(%#x)=%p body=%p %s; import %#x=%p hits=%u %s\n", (unsigned)FIRST_ADDR, (void *)first, (void *)FIRST_BODY,
           ok_first ? "match" : "MISMATCH", (unsigned)IMPORT_KEY, (void *)imp, fnvl_import_hits, ok_imp && fnvl_import_hits == 1 ? "match" : "MISMATCH");
    return ok_first && ok_imp && fnvl_import_hits == 1 ? 0 : 1;
}
