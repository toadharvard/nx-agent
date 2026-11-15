# l5 w14 -> L2: what the guest runtime (the none-elf mirror) must contain: acceptance list

From the checks of toolchain/gcc/check_mirror.py (C1-C8) and the patches of toolchain/gcc (x19-x27.patch, libstdcxx-nosized.patch, multilib.mk),
all of which the old two-stage build satisfied. The new mirror (msvcmodel GCC 13.3.0, --target=aarch64-none-elf, one build with its target libs and
newlib) is accepted when every line below holds. Each line names its check, so L2 can run it against its own build.

1. Compiler/target
   - gcc 13.3.0 + msvcmodel.patch (sha256 f85036408942152e...), --target=aarch64-none-elf, languages c,c++ (lto not needed), --disable-multilib is NOT
     used: the multilib table below is required. Binutils (as, ld, ar, ranlib, nm, objdump, objcopy, strip) 2.42, target aarch64-none-elf, same prefix.
   - `-print-multi-lib` has the ILP32 variants only with all three flags: -mabi=ilp32 -fshort-wchar -fsigned-char (+ fPIC). Table (multilib.mk):
       MULTILIB_OPTIONS=mabi=ilp32 fshort-wchar fsigned-char fPIC; MULTILIB_DIRNAMES=ilp32 wchar16 schar pic;
       MULTILIB_REQUIRED = fPIC  mabi=ilp32/fshort-wchar/fsigned-char  mabi=ilp32/fshort-wchar/fsigned-char/fPIC; MULTILIB_MATCHES=fPIC=fpic fPIC=fpie fPIC=fPIE.
     C1: every ILP32 entry carries -fshort-wchar and -fsigned-char; the only ILP32 library dirs are ilp32/wchar16/schar and ilp32/wchar16/schar/pic.
     C2: under the pack's own flags the driver answers that directory, it holds libc.a libstdc++.a libsupc++.a libgcc.a (and libgcc_eh.a, crt*.o) and a link
         with those flags succeeds; -mabi=ilp32 alone resolves elsewhere, holds no library, and its link fails.
2. Target libraries built by that compiler for each ILP32 variant, with the semantics flags of verify/guest_compiler.json:
   libgcc (+libgcc_eh), crti/crtn/crtbegin/crtend, newlib libc + libm (newlib 4.6.0.20260123, configure: --disable-newlib-supplied-syscalls
   --enable-newlib-mb --disable-newlib-wide-orient --enable-newlib-register-fini), libstdc++/libsupc++ 13.3.0 (configure of the old stage 2:
   --disable-libstdcxx-verbose --disable-libstdcxx-pch --enable-libstdcxx-time=yes --enable-cxx-flags=-ffunction-sections --disable-tm-clone-registry
   --disable-__cxa_atexit --with-newlib=yes --enable-threads=posix --with-march=armv8).
   - libsysbase is NOT part of this: newlib's syscall layer (_exit, __getreent, locks) comes from the platform, see L5; if the none-elf driver's default
     library group needs it, say so, L5 owns that answer.
3. Headers
   - the libstdc++ headers of the ILP32 variant are 13.3 (_GLIBCXX_RELEASE 13, 20240xxx) (C4); exactly one C++ include version, 13.3.0 (C7);
     newlib headers at the sysroot include; gcc's own <limits.h> includes newlib's (regenerated after newlib is installed: PATH_MAX must be visible).
4. Patches that stay (they are about the runtime, not the old route)
   - x19-x27.patch: no member of libgcc/newlib/crti writes x19..x27 (C8 reads the built objects; read-only mentions are allowed); newlib setjmp/longjmp and
     crti/crtn save no x19..x27. Sha256 recorded in build.sh (X19_PATCH_SHA256).
   - libstdcxx-nosized.patch: the two sized operator delete calls (fs_path.cc, memory_resource.cc) become the unsized forms, because the guest semantics
     has -fno-sized-deallocation; C6: no object of the library has an undefined reference to a sized or aligned-sized operator delete
     (the four mangled names come from the built compiler, not a list).
5. Data-model facts (C3, C5): __SIZEOF_WCHAR_T__ 2, __WCHAR_MAX__ 0xffff, no __CHAR_UNSIGNED__ for every ILP32 variant; wcslen etc. of the selected libc.a use
   ldrh/strh; every ELF object of the library trees is ELF32 AArch64 (C7); MANIFEST.json (C5) records gcc version, msvcmodel.patch sha256, flags,
   sizeof/alignof of wchar_t, size_t, long, long double (8 / 53 mantissa bits: newlib _LDBL_EQ_DBL derived, not set), pointer, read from the game compiler's
   object code, no run. verify/recipe.py runtime_manifest_errors compares them to the compiler before any unit runs.
6. Not in the mirror: libnx. It is stage 3 on top of the mirror (W1 patch toolchain/gcc/libnx-guest.patch, C8, C9), owned by w14, built after the mirror lands.
7. One configure line, recorded in toolchain/msvcmodel/build.sh (L2); the mirror's MANIFEST names it. Reproducible: two clean builds, equal tree_sha256.
