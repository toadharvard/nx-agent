# tests/bootguest

One guest image for the boot machine's tests (class `test`, built by verify/recipe.py, no variants). Its modes are the rows of `fnvboot_modes` in guest.c, the only list; a test reads the ids from the linked ELF (test_bootmachine.modes_of) and picks a mode by the run's input file `mode` (4 bytes, little endian) at the root of the declared card, sdmc:/mode (test_bootmachine.write_mode; farm/boot_job.sh takes it from the contract branch's `mode` file). main reads and closes it through the product fs path before any mode code; `fnvboot_mode_start` marks the end of that start-up, so assertions on host calls, events, icount and the clock are measured from it. A missing file exits 0xE0000002, an id that is no row of the table 0xE0000001. The image is the recipe's product byte for byte; no run edits it.

No product symbol is defined here: a test never defines a symbol that a product root defines (the doubles rule). Mode 92 (R-LOCKOBS record
mechanism) therefore has its own lock functions under non-product names (`t_acquire`, `t_try`, `t_release`) and verify/lockobs.py takes the
three watched names as arguments; its defaults are the os.h API (`os_lock`, `os_trylock`, `os_unlock`, trylock success = 1, os.h:42).

NOTE for tests/guest_os (step B, L4 w3b): the end-to-end A1/A2 check with the product's own lock functions belongs there. The image links
the real platform/guest/os_guest.c (os_lock/os_trylock/os_unlock at :47-49) and a watch file `os_lock` / `os_trylock ret` / `os_unlock` /
`<case runner> ret`; `boot.py run --watch-log` then `lockobs.py --out OUT.json --elf ELF --cases CASES.json` with the default names.
