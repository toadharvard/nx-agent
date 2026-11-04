/* litmus_mt.cpp - S12's standalone litmus test (tests/s12/order/litmus.cpp, unchanged) inside the mtqemu guest.
 *
 * Built twice by build.sh, both with -fsanitize=thread (hb.c is the runtime): LITMUS_NS=order with the fnvnx_order
 * plugin (the build's /volatile:ms semantics: volatile loads are acquires, stores releases), LITMUS_NS=plain
 * without it. The threads come from the real platform layer (litmus_glue.c: CreateThread over linhost, placed by
 * placement.h) instead of raw clone(), and every line the litmus prints carries the race count of that test.
 * On an x86-64 host qemu runs the threads in parallel on TSO hardware, so the "bad" outcome counts only catch the
 * compiler's reorderings; the race checker does not depend on the host: it sees whether each hand-off has a
 * happens-before edge in the AArch64 (C11) model, which is what the A57 needs. docs/PERF.md 6.6. */
#define main LITMUS_MAIN
namespace LITMUS_NS {
#include "../../s12/order/litmus.cpp"
}
