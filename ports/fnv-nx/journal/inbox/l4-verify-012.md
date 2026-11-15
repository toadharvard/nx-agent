# L4 VERIFY 012: the platform-lock observation as a machine record, rule R-LOCKOBS (item 15b, 2026-11-12 03:45Z)

Class: L5's lock harness (tests/s4/host_boundary.cpp, os_posix.c:94-149, census L5 w9 30dc0c0a: 584 runs) counts platform
locks with a double of os_lock/os_trylock/os_unlock and of the boundary entries. With doubles removed, the count is an
observation of the run by the shared model (the boot machine), and the assertions are the runner's.

## What is asserted (from w9, 03:38Z)
- A1: no platform lock is held by a thread when the platform enters game code (invoke by address, callback, guest thread
  start, process-detach hook). Violations over the whole run = 0, except a case that violates on purpose.
- A2: at the end of a case, its thread holds no platform lock (plus mid-case points such as test_resource.cpp:710).

## Rule R-LOCKOBS: the machine records the events from the image's own facts; the runner recomputes held lists
1. Lock events = the executions of the os.h API functions `os_lock`, `os_trylock`, `os_unlock`, resolved as exact function
   symbols of the ELF (the existing --watch-log resolution; an unresolved name is an error). At entry the record carries
   x0 (the lock address) and x30 (the call site). `os_trylock` is also recorded at its return (the pc = the entry x30, same
   tid, sp back to the entry sp) with w0, so `trylock_ok` is a fact of the run, not an assumption.
2. Game entry = a call (BL/BLR) whose source pc is outside the lifted section `.text.fnvl` and whose target is inside it.
   Section-based, the same rule for the product ELF (the lifted pack) and for a test image, where code standing for game
   code sits in `.text.fnvl` by its own source (as tests/bootguest fnvboot_*, w6 f36ee283). Record: tid, source pc,
   target pc. No list of boundary functions.
3. Case boundaries = the entry and return of the test root's own case-runner function, watched by symbol (a test-root
   function, not a product symbol). There are no marker slots, and no test-only host service.
4. Intentional violations are declared, not forgotten: the case's expectation lists the violations it must produce
   (count, and the game-entry target per violation). The runner compares exactly. A missing or an extra violation fails.
   This replaces fnvnx_bnd_violation_mark/forget.
5. Runner: per tid, held = multiset of lock addresses (+1 at os_lock entry and at a trylock_ok return, -1 at os_unlock of an
   address held by that tid; an unlock of an address that tid does not hold is recorded and ignored, as the harness does).
   A1 = at every game entry, held(tid) is empty. A2 = at every case return, held(tid) is empty. Mid-case checks become a
   game entry or a case boundary, or they are removed. Each removed check is listed with its reason. The list is not limited
   to 256/32 entries: the record is complete.

## Record format (OUT.json, under --watch-log; schema fnvnx-watch/2)
`{"seq","tid","t_ns","kind": "call"|"return"|"game-entry", "sym", "pc", "sp", "x0", "x30", "w0" (return only), "src","dst" (game-entry)}`.
`seq` is the machine order. `t_ns` is virtual time.

## Check that proves it
- test_bootmachine cases with hand-derived expectations: one lock/unlock pair; trylock fail and success; a nested pair; an
  unlock of a foreign address; a game entry with a lock held (1 violation) and without one (0); a declared violation that
  matches and one that does not.
- Then L5's lock assertions run on the record for all 584 runs, with 0 lock doubles.

Owner: L4 (machine record and runner, a worker); L5 w9 (moving the tests and the expectations). After the R-INPUT model.
