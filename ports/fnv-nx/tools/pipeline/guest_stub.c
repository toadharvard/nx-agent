/* tools/pipeline/guest_stub.c - the pipeline adapter's two pieces of the ILP32 guest link (stream S9-build).
 *
 * Used only by tools/pipeline/guest_link.py, i.e. until S5's toolchain/game/build.sh (with its boot code) is in
 * the tree; build.py switches to S5's script automatically when it appears.
 *
 *   __fnvnx_pipeline_trap: target of the generated trap stubs (one per symbol still undefined after the link),
 *                          reports the symbol and its caller and returns 0, so a partial image links and runs as far
 *                          as it can;
 *   main (weak):           only when no compiled unit defines main; the adapter has no boot code (S5's).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static unsigned fnvnx_pipeline_traps;

uint64_t __fnvnx_pipeline_trap(const char *name, uintptr_t lr)
{
	if (fnvnx_pipeline_traps < 64)
		printf("fnvnx guest (pipeline adapter): unresolved %s (caller 0x%08lx), returning 0\n", name,
		       (unsigned long)lr);
	if (++fnvnx_pipeline_traps >= 4096) {
		printf("fnvnx guest (pipeline adapter): %u unresolved calls, stopping\n", fnvnx_pipeline_traps);
		exit(3);
	}
	return 0;
}

__attribute__((weak)) int main(void)
{
	printf("fnvnx guest (pipeline adapter): static initialisation done, no boot code linked\n");
	return 1;
}
