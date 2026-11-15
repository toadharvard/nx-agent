/* main.c - the guest's process entry (the one definition of `main` in a product image; test images define their own and do not link this root).
 * crt0 -> __fnvnx_guest_entry (runtime.c: bss, relocation, TLS, newlib, files, init arrays) -> main -> fnvnx_loader_main, the loader's start
 * sequence (map, imports, TLS callbacks, DllMain, TEB/stack), which enters the exe through the lifter's fnvnx_lift_enter: the single bridge.
 * argc/argv/envp are what runtime.c passes; the loader takes no arguments (the exe's command line comes from the declared machine). */
#include "fnvnx_start.h"
#include "fnvnx/guest.h"

int main(int argc, char **argv, char **envp)
{
    (void)argc;
    (void)argv;
    (void)envp;
    return fnvnx_loader_main();
}
