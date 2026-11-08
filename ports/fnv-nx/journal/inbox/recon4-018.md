### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> R2's 'platform' undefined names, one status each | status: wip | parent: recon4-017

Source: fnv-nx-build s2d/list/97deacd38aec (undefined.tsv, causes.tsv, guest_trap_names.txt) and that head's
guest/fnvnx.elf. guest_trap_names.txt is read from the guest itself (stubs branching to __fnvnx_undef_trap), so a
name not in it and defined in the ELF is provided. The per-name status table stays in the scratchpad: it names
decomp symbols.

* **undefined.tsv, guest = platform: 432 names** (396 owner S4, 36 owner recon).
  - Provided by the platform layer at run time: **432 / 432**. Each is defined in the final guest ELF, and none is a
    trap stub. They are undefined in the trial link only because that link has no platform objects.
  - Missing implementation: 0. Another stream's: 0.
* **The platform names that really are missing** are the trap stubs that causes.tsv classes `platform` and
  `runtime` (COUNTS.md: 30 + 36, all trapped).
  - platform 30: the `__fnvnx_tls_<addr>` slots and `__fnvnx_tls_image`. These are the link's TLS block binding,
    **s2-bodies'**. Flag: a data trap stub for `__fnvnx_tls_image` means boot.cpp's image TLS block hook would
    hand out a plain 4 KiB .bss object, not a thread-local block. Worth checking in the link.
  - runtime 36:
    - **10 are S4's**, and now provided (commit below): std::_Lockit(int), ~_Lockit,
      _Locinfo::_Locinfo_ctor(_Locinfo*, const string&), _Locinfo_dtor, bad_cast(const char*),
      locale::facet::_Incref, facet_Register, locale::id::_Id_cnt, __isoc23_strtoul and __errno_location. They
      forward to the existing fnvnx_* providers; _Incref and _Id_cnt are new.
    - **26 are s2-bodies'**: template instances the units never emit (vector / fill / _Allocate / _Destroy / swap /
      _Xlen of the decomp's containers), the decomp's own std look-alikes (basic_ifstream::open, ios_base::setstate,
      basic_ios::init, CsvUseFacet, FUN_00ad5770), and StandardLibrarySingletonObject ctor/dtor (a decomp
      invention).
* **measured** - S4's real missing platform names: 10 -> 0. The 9 C++ names match R2's mangled names exactly
  (compiled -mabi=ilp32, nm against causes.tsv). __errno_location is defined only off glibc: on a glibc host errno
  itself calls it. The guest's newlib build defines it.
* **tests** - s4: 108 tests, 0 failed. s7: 22 tests, 0 failures. ILP32 syntax check of msvc_std_names.cpp: clean.
