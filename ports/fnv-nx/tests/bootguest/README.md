# tests/bootguest

Guest images of the boot machine's tests (class `test`, one variant `m<MODE>` per MODE, built by verify/recipe.py).

No product symbol is defined here: a test never defines a symbol that a product root defines (the doubles rule). MODE 92 (R-LOCKOBS record
mechanism) therefore has its own lock functions under non-product names (`t_acquire`, `t_try`, `t_release`) and verify/lockobs.py takes the
three watched names as arguments; its defaults are the os.h API (`os_lock`, `os_trylock`, `os_unlock`, trylock success = 1, os.h:42).

NOTE for tests/guest_os (step B, L4 w3b): the end-to-end A1/A2 check with the product's own lock functions belongs there. The image links
the real platform/guest/os_guest.c (os_lock/os_trylock/os_unlock at :47-49) and a watch file `os_lock` / `os_trylock ret` / `os_unlock` /
`<case runner> ret`; `boot.py run --watch-log` then `lockobs.py --out OUT.json --elf ELF --cases CASES.json` with the default names.
