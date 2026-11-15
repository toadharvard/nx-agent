/* main.c - the guest's process entry (the one definition of `main` in a product image: the product ELF root declares `link.entry` main; a test image declares its
 * own `link.entry` symbol, which no product root defines, so it neither links this root nor defines `main`).
 * crt0 -> __fnvnx_guest_entry (runtime.c: bss, relocation, TLS, newlib, files, init arrays) -> __fnvnx_image_entry (= main) -> fnvnx_loader_main, the loader's start
 * sequence (map, imports, TLS callbacks, DllMain, TEB/stack), which enters the exe through the lifter's fnvnx_lift_enter: the single bridge.
 * argc/argv/envp are what runtime.c passes; the loader takes no arguments (the exe's command line comes from the declared machine). */
#include "fnvnx_start.h"

int main(int argc, char **argv, char **envp)
{
    (void)argc;
    (void)argv;
    (void)envp;
    return fnvnx_loader_main();
}
