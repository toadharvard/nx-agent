/* TEST FIXTURE of link/tests/real_reloc.py: data words whose link-time value lies outside [0, image size) or on its end, in a guest linked at
 * base 0 and moved by the loader.  Each word is an R_AARCH64_P32_RELATIVE entry (RELA or RELR) naming an image address; after the guest's
 * self-relocation each must equal the address the code computes PC-relatively for the same symbol.
 *   past_end    __end__ is the image size: one past the last mapped byte (value == size);
 *   below_base  __fnvnx_guest_base - 4: link-time value 0xfffffffc, an addend below the link base;
 *   inside      __fnvnx_guest_base + 8: the control, inside the range. */
#include <stdint.h>
#include <stdio.h>

/* hidden: the code takes their addresses PC-relatively (adrp), not through the GOT, which the self-relocation also fills */
extern char __end__[] __attribute__((visibility("hidden")));
extern char __fnvnx_guest_base[] __attribute__((visibility("hidden")));

static char *const past_end = __end__;
static char *const below_base = __fnvnx_guest_base - 4;
static char *const inside = __fnvnx_guest_base + 8;

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    int a = past_end == __end__, b = below_base == __fnvnx_guest_base - 4, c = inside == __fnvnx_guest_base + 8;
    printf("reloc edge: past_end %s (%p vs %p), below_base %s (%p vs %p), inside %s\n", a ? "ok" : "WRONG", (void *)past_end, (void *)__end__,
           b ? "ok" : "WRONG", (void *)below_base, (void *)(__fnvnx_guest_base - 4), c ? "ok" : "WRONG");
    return a && b && c ? 0 : 1;
}
