#!/usr/bin/env python3
"""import_escape.py - per-argument pointer escape and write summary of the platform-layer imports.

For every bridge-table row whose native_symbol is defined in our platform sources, emit two strings, one letter per
x86 argument slot each (one letter per bridge `args` letter; q = one letter):
 escape:
  n  the pointer in this slot is not captured: after the call returns no copy of it (or of a pointer derived
     from it) remains anywhere (global, heap, another thread, the OS) and it is not returned.
     Integer / floating slots are n.
  r  it may be returned (or a pointer derived from it) but is otherwise not captured.
  e  it escapes, or nothing could be proven.
 writes:
  n  the import provably does not WRITE through this pointer: LLVM's inter-procedural inference (same pipeline
     as below) gives the parameter the attribute `readonly` or `readnone` (LangRef: readonly = "the function does
     not write through this pointer argument, even though it may write to the memory that the pointer points to";
     readnone = "does not dereference this pointer argument"). Claimed: no store, atomic read-modify-write, memory
     intrinsic or callee write occurs, inside the import or anything it calls, through this pointer value or any
     pointer derived from it (GEP/cast/phi/select chains) - one level: memory reached by loading another pointer out
     of the pointee is NOT covered. NOT claimed: that the pointee bytes stay unmodified - another alias (a second
     argument slot with the same address, a global, a copy captured earlier by another import, another thread) may
     be written by the import or by a later call; an escape letter e (or r) on the slot means a later
     write through a retained copy is possible. "The pointee is not modified by this call or afterwards through
     this pointer" needs escape=n AND writes=n of that slot (aliasing through other slots is that slot's own fact).
     Non-pointer slots are n.
  y  may write, or nothing proven (a pointer passed to a callee without a definition and without a DECLARED
     contract; a pointer that is stored, converted to an integer, or reaches any use LLVM does not classify).

Method (a sound compiler analysis, no hand-written lists):
  1. every source -> LLVM bitcode (clang-18 -O1 -emit-llvm, host x86-64 LP64, flags of tests/s7/Makefile);
  2. llvm-link into ONE module (no internalize: every import entry point keeps external linkage);
  3. text-level rewrite of `volatile` on loads/stores/atomicrmw/cmpxchg whose VALUE operand is not a pointer
     (see strip_volatile for the argument), then opt-18 -passes='default<O2>'. LLVM's inter-procedural
     function-attrs inference (cgscc function-attrs, rpo-function-attrs, inliner) then sets the parameter
     attribute `nocapture` where the callee provably makes no copy that outlives it (LangRef);
  4. the parameter attribute of each native_symbol gives the letter. Pointer passed to a function without a
     definition in the module stays captured (clang gives libc declarations their TLI attributes; nothing is
     added by hand);
  5. `returned`: LLVM counts returning the pointer as a capture. r is emitted only for a parameter that has the
     `returned` attribute AND whose parameter of a generated wrapper `void w(args){ f(args); }` (noinline, in
     the same linked module, result discarded) is nocapture after the same pipeline. A function that returns
     a pointer DERIVED from the argument does not get `returned` and stays e.
6. calls that leave the module (hooks of other components, host services, target libc) give a fact only from
   attributes on the declaration: those clang emits from source (e.g. noescape), the C library knowledge LLVM
   itself has for standard functions (TargetLibraryInfo; nothing added by hand), and the DECLARED contracts of
   `--contract <tsv>` (symbol, 0-based param index, attrs among nocapture/readonly/readnone, source). A contract is
   applied to declarations only; the tool fails if a contract names a function defined in the module. Applying
   `readonly`/`nocapture` to a declaration is sound exactly when the declared contract holds, which the named
   source (the owner's statement) vouches for; clang has no source spelling that yields `readonly` on a parameter
   (access(read_only) is ignored by clang 18, `noescape` yields only nocapture), hence the tsv.
7. `icmp eq/ne ptr %x, null` tests are replaced before the pipeline by calls of an opaque, unconstrained, non-capturing
   function (hide_null_tests: LLVM 18 would otherwise count every null test as a capture); only adds behaviours.
Mismatches between the bridge `args` and our C prototype (LLVM parameter count / kind), vararg handling,
missing definitions: rows are omitted and listed in the report.
"""
import argparse, concurrent.futures as cf, glob, os, re, subprocess, sys, tempfile

CLANG = 'clang-18'
LINK = 'llvm-link-18'
DIS = 'llvm-dis-18'
OPT = 'opt-18'
PIPELINE = "default<O2>"

HEADER = """\
# import_escape.tsv - per-argument pointer escape and write summary of the platform-layer imports (generated by import_escape.py)
# escape: one letter per bridge `args` letter (q = one letter): n = pointer not captured (no copy outlives the call,
#   not returned); r = may be returned (or derived pointer returned), otherwise not captured; e = escapes / unknown.
#   Integer and floating slots are n. Vararg: fixed slots then literal `*e`. A missing row means all-e.
# n is LLVM's `nocapture` after inter-procedural inference on the whole linked platform module (pipeline line below).
#   Thread-safety note: a function that hands the pointer to another thread necessarily stores it somewhere
#   (queue, global, heap object, pthread_create argument, OS call) and is therefore not nocapture, hence e.
# writes: same layout as escape (one letter per args letter, vararg tail literal `*y`, missing row = all-y):
#   n = the import provably does not write through this pointer = LLVM parameter attribute `readonly` or `readnone`
#   after the same inference. LangRef: readonly = "the function does not write through this pointer argument, even
#   though it may write to the memory that the pointer points to"; readnone = "does not dereference this pointer
#   argument". Claimed: no store / atomic RMW / memory intrinsic / callee write happens (in the import or anything it
#   calls) through this pointer or a pointer derived from it (GEP, casts, phi, select); one level only (memory reached
#   by loading another pointer out of the pointee is not covered). NOT claimed: that the pointee is never modified:
#   another alias (a second slot with the same address, a global, a copy captured earlier by another import, another
#   thread) may be written, and a slot with escape e/r may be written later through a retained copy. Unmodified pointee
#   = escape n and writes n of the slot. y = may write or nothing proven. Integer and floating slots are n.
# Calls leaving the module (hooks of other components, host services, target libc) carry facts only from attributes on
#   their declaration: clang's own, LLVM's built-in C library knowledge, and the declared contracts listed below
#   (`# contract:` lines; each names its source). Nothing else is assumed about them.
# Null tests: `icmp eq/ne ptr %x, null` is not counted as a capture (one bit, no copy; see hide_null_tests for the
#   soundness argument). Any other use of the address (ordering / equality of two pointers, ptrtoint, store) still is.
# ASSUMPTION (target substitution): clang-18 cannot emit the product triple (AArch64 ILP32 ELF), the guest table is
#   built for arm64_32-apple-watchos (AArch64, 32-bit pointers/long, ILP32 data layout; Mach-O/Darwin ABI, Apple macros
#   undefined again). `nocapture` and `readonly` are properties of the IR use graph of a pointer parameter (which
#   instructions use it and what the callees' attributes are); they do not depend on the object format, the section
#   choices, or the calling-convention details of scalar pointers/integers. What the ABI can change: va_list (char*
#   here, a struct in AAPCS64), long double, and the passing of structs/unions/arrays by value (byval / sret / split
#   registers), which alter the LLVM parameter list of a prototype. A row whose prototype has such a construct fails the
#   parameter-count / kind / aggregate check and is OMITTED and listed in the report, never emitted from this target.
#   Pointer-width dependent code (uint32_t guest address <-> pointer) is analysed with the real 32-bit width. Residual
#   risk: code whose control flow depends on ABI macros (__APPLE__ is undefined, __aarch64__ / __ILP32__ are as in the
#   product); the sources' own preprocessor conditions are those of the product build for these macros.
# Assumptions: volatile accesses are plain memory accesses for capture purposes (they copy no pointers); an integer
#   parameter is not a pointer; the backend line below names the OS backend analysed; pointers are not smuggled into
#   integers that outlive the call (inttoptr/ptrtoint chains are not followed for integer parameters).
"""


def die(msg):
    sys.stderr.write('import_escape: ' + msg + '\n')
    sys.exit(2)


def run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', **kw)


def tail(text, n=8):
    return ' | '.join(text.strip().splitlines()[-n:])[:600]


# ---------------------------------------------------------------- sources and flags
def host_flags(root):
    P = os.path.join(root, 'platform')
    flags = ['-O1', '-frounding-math', '-fshort-wchar', '-pthread', '-w',
             '-I' + P + '/include', '-iquote', root + '/shim/include', '-I' + root + '/shim/include',
             '-I' + P + '/crt', '-I' + root + '/ext/minimp3']
    mk = os.path.join(root, 'tests/s7/Makefile')
    if os.path.exists(mk):  # flags are taken from the Makefile (FLAGS := ...), host-irrelevant ones dropped
        txt = open(mk).read().replace('\\\n', ' ')
        m = re.search(r'^FLAGS\s*:=\s*(.*)$', txt, re.M)
        if m:
            toks = m.group(1).replace('$(P)', P).replace('$(ROOT)', root).split()
            out, skip = [], False
            for t in toks:
                if skip:
                    skip = False; out.append(t); continue
                if t in ('-MMD', '-MP', '-g', '-pthread') or t.startswith('-W'):
                    continue
                out.append(t)
                if t == '-iquote':
                    skip = True
            flags = out + ['-pthread', '-w']
    return flags


def target_flags(root, newlib, backend):
    """Horizon backend flags (aarch64-none-elf LP64, newlib headers). The guest product uses guest_product().
    """
    P = os.path.join(root, 'platform'); T = os.path.join(root, 'toolchain')
    f = ['--target=aarch64-none-elf', '-march=armv8-a+crc+crypto', '-O1', '-frounding-math', '-fshort-wchar', '-fsigned-char',
         '-w', '-DSTDMETHODCALLTYPE=', '-I' + P + '/include', '-iquote', root + '/shim/include',
         '-I' + root + '/shim/include', '-I' + P + '/crt', '-I' + T + '/include', '-I' + root + '/startup/guest',
         '-I' + root + '/ext/minimp3', '-isystem', newlib + '/machine/aarch64', '-isystem', newlib + '/include']
    return f


def find_sources(root, backend):
    """-> list of (source, extra flags)."""
    P = os.path.join(root, 'platform'); T = os.path.join(root, 'toolchain')
    srcs = []
    for p in ['win32/*.cpp', 'crt/*.c', 'crt/*.cpp', 'audio/*.cpp', 'input/*.cpp']:
        srcs += glob.glob(os.path.join(P, p))
    out = []
    for s in sorted(srcs):
        if s.endswith('input_horizon.cpp') and backend != 'horizon':
            continue   # guest: its libnx part is on the host (toolchain/game/build.sh)
        out.append((s, []))
    out.append((P + '/os/os_world.c', []))   # the world registry, common to the three backends
    if backend == 'posix':
        out.append((P + '/os/os_posix.c', []))
        out.append((P + '/audio/backend_audren.c', []))
    elif backend == 'horizon':
        out.append((P + '/os/os_horizon.c', ['-D__SWITCH__']))
        out.append((P + '/audio/backend_audren.c', ['-D__SWITCH__']))
    return out


GUEST_TRIPLE = 'arm64_32-apple-watchos'
# platform/build_guest.sh HOST_SIDE: platform files that are on the host (LP64 / libnx), not in the guest archive
HOST_SIDE = ('platform_host/input/input_horizon.cpp', 'platform_host/audio/backend_audren.c', 'platform_host/os/os_horizon.c',
             'platform_host/os/os_posix.c', 'platform_host/win32/filemap_shared.cpp')


def make_var(root, name):
    r = run(['make', '-s', '-f', root + '/tests/s4/Makefile', '--eval', 'pv_%s: ; @echo $(%s)' % (name, name), 'pv_' + name])
    if r.returncode or not r.stdout.strip():
        die('cannot read %s from tests/s4/Makefile: %s' % (name, tail(r.stderr)))
    return r.stdout.split()


def guest_product(root, tc, libnx_inc, triple):
    """The PRODUCT source set and flags of the guest (platform/build_guest.sh + toolchain/guest/build.sh), as clang jobs.
    -> (jobs [(source, full flag list)], triple description).
    Source set (exactly build_guest.sh): every .c/.cpp (maxdepth 1) of platform/{win32,crt,audio,input,boundary,com,os}
    minus HOST_SIDE, platform/clock/{clock_rt,clock_real}.cpp (the product clock), platform/guest/{os_guest.c,platform_glue.cpp}, the FreeType sources of tests/s4/Makefile (FT_SRCS),
    plus the guest runtime libfnvnx-guest.a (toolchain/guest: runtime.c threads.c fs.c section_guest.c tm.cpp): the guest
    image links all of them, so they are product code, not an unknown callee. Generated .inc/.gen files must exist
    (platform/build_guest.sh generates them; `make -C tests/s4 <ENV_GEN, ENVC_SRC .env_const.gen.inc, combridge.gen.cpp>`).
    Flags: build_guest.sh COMMON/PINC/ARCH with the differences the clang front end forces, all of them IR-neutral for
    the facts: -O1 per file (the full -O2 pipeline runs on the linked module), no -g, no -ffunction-sections /
    -fdata-sections / -fPIE / -ftls-model (section / relocation choices, no effect on IR attributes).
    Target: clang-18 cannot target AArch64 ILP32 ELF (aarch64-linux-gnu_ilp32 stops with "backend data layout ... does
    not match expected target description": the LLVM backend knows p:32:32, clang's TargetInfo does not). The closest
    triple clang-18 accepts is arm64_32-apple-watchos: AArch64 with 32-bit pointers / 32-bit long, the data layout of
    ILP32 (-D__ILP32__ is predefined), Mach-O/Darwin ABI instead of ELF/AAPCS64 (va_list is a char*, not the AAPCS
    struct; Apple macros are undefined again so newlib / libstdc++ headers take their ELF paths). The ABI differences
    can change the LLVM parameter shape of a prototype that takes or returns aggregates / va_list; such rows then fail
    the prototype check and are reported, not guessed."""
    P = root + '/platform'; T = root + '/toolchain'
    ext = root + '/ext'
    res = run([CLANG, '-print-resource-dir']).stdout.strip()
    dm = run([CLANG, '--target=' + triple, '-dM', '-E', '-x', 'c', '/dev/null']).stdout
    ilp32 = '#define __ILP32__ 1' in dm
    undef = ['-U' + m for m in ('__APPLE__', '__APPLE_CC__', '__MACH__') if '#define %s ' % m in dm]
    base = ['--target=' + triple, '-nostdinc', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fsigned-char',
            '-frounding-math', '-fshort-wchar', '-O1', '-w'] + undef
    if not ilp32:
        base.append('-D__ILP32__')   # LP64 fallback triple only (hostapi.h keys fnvnx_hcall* on it)
    pinc = ['-I' + P + '/include', '-iquote', root + '/shim/include', '-I' + root + '/shim/include', '-I' + P + '/crt',
            '-I' + T + '/include', '-I' + root + '/loader/include', '-I' + ext + '/minimp3', '-I' + ext + '/freetype/include',
            '-D__LINUX_ERRNO_EXTENSIONS__']
    nl = tc + '/aarch64-none-elf/include'
    cxxd = nl + '/c++/15.2.0'
    if not os.path.isdir(nl + '/stdio.h') and not os.path.exists(nl + '/stdio.h'):
        die('toolchain newlib headers not found at %s (--tc: toolchain prefix)' % nl)
    cxx_sys = ['-isystem', cxxd + '/aarch64-none-elf' + ('/ilp32' if ilp32 else ''), '-isystem', cxxd]
    sysinc = ['-isystem', res + '/include', '-isystem', nl]
    after = ['-idirafter', libnx_inc]
    cflags = base + ['-std=gnu11', '-x', 'c'] + pinc + sysinc + after
    xflags = base + ['-std=gnu++17', '-x', 'c++', '-DSTDMETHODCALLTYPE=', '-DFNVNX_PLATFORM_BUILD',
                              '-include', 'stdlib.h'] + pinc + cxx_sys + sysinc + after

    def fl(s):
        return cflags if s.endswith('.c') else xflags
    jobs = []
    for sub in ('win32', 'crt', 'audio', 'input', 'boundary', 'com', 'os'):
        d = os.path.join(P, sub)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            s = os.path.join(d, n)
            if n.endswith(('.c', '.cpp')) and os.path.isfile(s) and os.path.relpath(s, root) not in HOST_SIDE:
                jobs.append((s, fl(s)))
    # the clock of the product (FNVNX_CLOCK=real, build_guest.sh default): clock_rt.cpp + clock_real.cpp, never virtual
    for n in ('clock_rt.cpp', 'clock_real.cpp'):
        jobs.append((P + '/clock/' + n, fl(n)))
    for n in ('os_guest.c', 'platform_glue.cpp'):
        jobs.append((P + '/guest/' + n, fl(n)))
    ft_flags = [f if f != '-O2' else '-O1' for f in make_var(root, 'FT_CFLAGS')]
    ftb = [f for f in base if not f.startswith('-std')]
    for m in make_var(root, 'FT_SRCS'):
        jobs.append((ext + '/freetype/src/' + m + '.c', ftb + ['-std=gnu11', '-x', 'c'] + sysinc + ft_flags))
    rtc = base + ['-std=gnu11', '-x', 'c', '-I' + T + '/include'] + sysinc
    rtx = base + ['-std=gnu++17', '-x', 'c++'] + cxx_sys + sysinc
    for n in ('runtime.c', 'threads.c', 'fs.c', 'section_guest.c'):
        jobs.append((T + '/guest/' + n, rtc))
    jobs.append((T + '/guest/tm.cpp', rtx))
    return jobs, '%s (%s)' % (triple, 'ILP32 data layout' if ilp32 else 'LP64 data layout')


def libnx_sources(libnx):
    srcs = []
    for dp, dn, fn in os.walk(os.path.join(libnx, 'nx', 'source')):
        for n in fn:
            if n.endswith(('.c', '.cpp')):
                srcs.append(os.path.join(dp, n))
    return sorted(srcs)


def svc_names(libnx):
    names = set()
    p = os.path.join(libnx, 'nx/source/kernel/svc.s')
    if os.path.exists(p):
        names = set(re.findall(r'^\s*SVC_BEGIN\s+(\w+)', open(p).read(), re.M))
    return names


def compile_one(job, work):
    src, flags = job
    base = os.path.relpath(src, '/').replace('/', '_')
    bc = os.path.join(work, base + '.bc')
    std = ['-std=gnu++17', '-x', 'c++'] if src.endswith(('.cpp', '.cc')) else ['-std=gnu11', '-x', 'c']
    r = run([CLANG, '-emit-llvm', '-c'] + std + flags + [src, '-o', bc])
    return src, (bc if r.returncode == 0 else None), r.stderr


# ---------------------------------------------------------------- IR text handling
VOL_LOAD = re.compile(r'\b(load( atomic)?) volatile ')
VOL_STORE = re.compile(r'\b(store( atomic)?) volatile (?!ptr\b)')
VOL_RMW = re.compile(r'\batomicrmw volatile (\w+) (ptr(?: addrspace\(\d+\))?) (\S+), (?!ptr\b)')
VOL_CX = re.compile(r'\bcmpxchg (weak )?volatile (ptr(?: addrspace\(\d+\))?) (\S+), (?!ptr\b)')


def strip_volatile(ir):
    """LLVM treats every volatile access through a pointer as a capture of it (CaptureTracking: 'volatile
    operations effectively capture the memory location'). That models MMIO, not a copy of the pointer value
    that outlives the call; no volatile access of any kind stores the ADDRESS operand anywhere. We therefore
    clear the volatile flag on accesses whose accessed VALUE is not a pointer (a pointer-typed value stored
    by a volatile store / atomicrmw / cmpxchg keeps volatile, hence stays a capture of that value). Clearing
    the flag only enables more optimisation of the same memory operations; it creates no copies of any pointer.
    Assumption stated in the output header: volatile is not used as a side channel that retains the address."""
    ir = VOL_LOAD.sub(r'\1 ', ir)
    ir = VOL_STORE.sub(lambda m: m.group(1) + ' ', ir)
    ir = VOL_RMW.sub(lambda m: 'atomicrmw %s %s %s, ' % (m.group(1), m.group(2), m.group(3)), ir)
    ir = VOL_CX.sub(lambda m: 'cmpxchg %s%s %s, ' % (m.group(1) or '', m.group(2), m.group(3)), ir)
    return ir


NULLCMP = re.compile(r'^(\s*)(%[\w.$-]+) = icmp (eq|ne) ptr (?:null, (%[\w.$-]+)|(%[\w.$-]+), null)\s*$', re.M)


def hide_null_tests(ir):
    """LLVM 18 counts `icmp eq/ne ptr %x, null` as a capture of %x unless %x is known dereferenceable (CaptureTracking:
    comparing a pointer with null leaks one bit of the address). A null test creates no copy of the pointer: the result
    is one bit, nothing is stored, nothing is read or written through it, so it cannot make a pointer outlive the call,
    and `if (!name) return` is what every import does with its string arguments. Each such test is replaced by a call
    of an opaque function whose result is UNCONSTRAINED (fnvw_isnull for eq, fnvw_notnull for ne; declared nounwind
    willreturn memory(none), parameter nocapture readnone). Soundness: the replacement can only add behaviours
    (the bit is any value instead of the real comparison; eq and ne are independent), never remove one, so every
    attribute proven on the rewritten module holds for the original: its executions are a subset. Only comparisons
    against the null constant are rewritten; comparisons of two pointers (address ordering / equality of two non-null
    values) stay captures."""
    def f(m):
        x = m.group(4) or m.group(5)
        return '%s%s = call i1 @fnvw_%s(ptr %s)' % (m.group(1), m.group(2), 'isnull' if m.group(3) == 'eq' else 'notnull', x)
    out, n = NULLCMP.subn(f, ir)
    if n:
        out += ('\ndeclare i1 @fnvw_isnull(ptr nocapture readnone) nounwind willreturn memory(none)\n'
                'declare i1 @fnvw_notnull(ptr nocapture readnone) nounwind willreturn memory(none)\n')
    return out, n


DEF_RE = re.compile(r'^define\s+(.*?)@("(?:[^"\\]|\\.)*"|[\w.$-]+)\(')


def split_top(s):
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in '({[<':
            depth += 1
        elif ch in ')}]>':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if ''.join(cur).strip():
        parts.append(''.join(cur).strip())
    return parts


def parse_params(line, start):
    """-> (params, vararg, ret_type_text) ; params = list of dict(type, attrs, text)."""
    depth, i = 1, start
    while i < len(line) and depth:
        if line[i] == '(':
            depth += 1
        elif line[i] == ')':
            depth -= 1
        i += 1
    inner = line[start:i - 1]
    params, vararg = [], False
    for p in split_top(inner):
        if p == '...':
            vararg = True
            continue
        if p[0] in '{<[':
            ty = 'aggregate'
        else:
            ty = p.split()[0]
            if ty == 'ptr':
                ty = 'ptr'
        # attribute words only: drop the parameter's own name (%x / %"x") and attribute payloads in parentheses
        bare = re.sub(r'\([^()]*\)', '', re.sub(r'%("(?:[^"\\]|\\.)*"|[\w.$-]+)', '', p))
        words = set(re.findall(r'[A-Za-z_]+', bare))
        params.append({'type': ty, 'text': p, 'nocapture': 'nocapture' in words, 'returned': 'returned' in words,
                       'readonly': bool(words & {'readonly', 'readnone'}),
                       'byval': bool(words & {'byval', 'sret', 'inalloca', 'byref'})})
    return params, vararg


def parse_defs(ir):
    defs, declared = {}, set()
    for line in ir.splitlines():
        if line.startswith('define '):
            m = DEF_RE.match(line)
            if not m:
                continue
            name = m.group(2).strip('"')
            pre = m.group(1)
            params, vararg = parse_params(line, m.end())
            defs[name] = {'params': params, 'vararg': vararg, 'internal': bool(re.match(r'(internal|private)\b', pre)),
                          'pre': pre}
        elif line.startswith('declare '):
            m = re.search(r'@("(?:[^"\\]|\\.)*"|[\w.$-]+)\(', line)
            if m:
                declared.add(m.group(1).strip('"'))
    return defs, declared


def ret_type_of(pre):
    # `pre` = text between 'define' and '@name': linkage/cc/ret attrs then the return type (last token group)
    toks = pre.split()
    # return type is the last token that is a type; attributes such as noundef/noalias precede it
    return toks[-1] if toks else 'void'


# ---------------------------------------------------------------- bridge
def read_bridge(path):
    rows = []
    with open(path) as f:
        hdr = f.readline().rstrip('\n').split('\t')
        for line in f:
            if not line.strip():
                continue
            c = line.rstrip('\n').split('\t')
            c += [''] * (len(hdr) - len(c))
            rows.append(dict(zip(hdr, c)))
    return rows


def kind_ok(letter, ty):
    if letter in 'fd':
        return ty in ('float', 'double')
    if letter == 'q':
        return ty.startswith('i')
    return ty == 'ptr' or (ty.startswith('i') and ty[1:].isdigit())


# ---------------------------------------------------------------- what-if variants (reporting only)
INDIRECT = re.compile(r'^(\s*)(?:(%[\w.$-]+) = )?(?:(?:tail|musttail|notail) )?call\s+[^@\n]*?(\S+)\s+%[\w.$-]+\(.*$')


def drop_indirect(ir):
    """What-if H: remove every call through a pointer value (host-API table). The call's result becomes 0."""
    out = []
    for line in ir.split('\n'):
        m = INDIRECT.match(line) if ' call ' in line and '@' not in line.split(' call ', 1)[1].split('(')[0] else None
        if not m:
            out.append(line); continue
        ind, res, ty = m.group(1), m.group(2), m.group(3)
        if res is None or ty == 'void':
            continue
        if ty == 'ptr':
            out.append('%s%s = getelementptr i8, ptr null, i64 0' % (ind, res))
        elif ty in ('float', 'double'):
            out.append('%s%s = fadd %s 0.0, 0.0' % (ind, res, ty))
        elif ty.startswith('i') and ty[1:].isdigit():
            out.append('%s%s = add %s 0, 0' % (ind, res, ty))
        else:
            out.append(line)   # aggregate result: keep the call
    return '\n'.join(out)


def svc_nocapture(ir, svcs):
    """What-if S: the svc wrappers (assembly, no bitcode) declared with every pointer parameter nocapture."""
    out = []
    for line in ir.split('\n'):
        if line.startswith('declare '):
            m = re.search(r'@("[^"]*"|[\w.$-]+)\(', line)
            if m and m.group(1).strip('"') in svcs:
                i = m.end()
                line = line[:i] + re.sub(r'\bptr\b', 'ptr nocapture', line[i:])
        out.append(line)
    return '\n'.join(out)


def calls_to(ir, names):
    """Declared-only functions of `names` that the module actually calls (for the report)."""
    return sorted(n for n in names if re.search(r'@' + re.escape(n) + r'\(', ir))


def opt_variant(work, tag, ir, pipeline):
    p = os.path.join(work, 'v_%s.ll' % tag)
    q = os.path.join(work, 'v_%s_opt.ll' % tag)
    open(p, 'w').write(ir)
    r = run([OPT, '-passes=' + pipeline, p, '-S', '-o', q])
    if r.returncode:
        return None
    return parse_defs(open(q).read())[0]


# ---------------------------------------------------------------- declared contracts (external declarations only)
CONTRACT_ATTRS = ('nocapture', 'readonly', 'readnone')
DECL_RE = re.compile(r'^declare\b[^\n@]*@("(?:[^"\\]|\\.)*"|[\w.$-]+)\(', re.M)
contract_used = set()


def read_contracts(path):
    """tsv with header `symbol param attrs source`; param is the 0-based parameter index, attrs a comma list of
    nocapture / readonly / readnone, source the owner's statement. -> [(symbol, index, [attrs], source)]."""
    rows = []
    with open(path) as f:
        hdr = f.readline().rstrip('\n').split('\t')
        if hdr[:4] != ['symbol', 'param', 'attrs', 'source']:
            die('contract file %s: header must be symbol, param, attrs, source' % path)
        for n, line in enumerate(f, 2):
            if not line.strip() or line.startswith('#'):
                continue
            c = line.rstrip('\n').split('\t')
            if len(c) < 4 or not c[0] or not c[1].isdigit() or not c[3].strip():
                die('contract file %s line %d: need symbol, numeric param, attrs, source' % (path, n))
            attrs = [x for x in c[2].split(',') if x]
            if not attrs or any(x not in CONTRACT_ATTRS for x in attrs):
                die('contract file %s line %d: attrs must be among %s' % (path, n, '/'.join(CONTRACT_ATTRS)))
            rows.append((c[0], int(c[1]), attrs, c[3].strip()))
    return rows


def declare_line(ir, sym):
    for m in DECL_RE.finditer(ir):
        if m.group(1).strip('"') == sym:
            e = ir.index('\n', m.start())
            return m.start(), e
    return None


def declare_params(line, start):
    """-> (inner start, inner end, [param text]) of the declare line's parameter list."""
    depth, i = 1, start
    while i < len(line) and depth:
        depth += (line[i] == '(') - (line[i] == ')')
        i += 1
    return start, i - 1, split_top(line[start:i - 1])


def add_param_attrs(ir, sym, idx, attrs):
    """Adds the parameter attributes to parameter idx (a ptr) of the DECLARATION of sym. -> new ir, or None if there is
    no declaration of sym in the module."""
    pos = declare_line(ir, sym)
    if pos is None:
        return None
    line = ir[pos[0]:pos[1]]
    m = re.search(r'@(?:"(?:[^"\\]|\\.)*"|[\w.$-]+)\(', line)
    a, b, ps = declare_params(line, m.end())
    if idx >= len([p for p in ps if p != '...']) or not ps[idx].startswith('ptr'):
        die('contract names parameter %d of %s which is not a pointer parameter of its declaration: %s' % (idx, sym, line[:200]))
    have = set(re.findall(r'[A-Za-z_]+', ps[idx]))
    ps[idx] = 'ptr ' + ' '.join(x for x in attrs if x not in have) + ' ' + ps[idx][3:].lstrip()
    return ir[:pos[0]] + line[:a] + ', '.join(ps) + line[b:] + ir[pos[1]:]


def apply_contracts(ir, contracts):
    """Applies the DECLARED contracts to declarations. A contract naming a function that is defined in the module is an
    error (a defined function is analysed, never assumed)."""
    defs, _ = parse_defs(ir)
    for sym, idx, attrs, src in contracts:
        if sym in defs:
            die('contract names %s, which is DEFINED in the module (%s): contracts apply to declarations only' % (sym, src))
        r = add_param_attrs(ir, sym, idx, attrs)
        if r is not None:
            ir = r
            contract_used.add(sym)
    return ir


# ---------------------------------------------------------------- blockers (report only)
CALL_RE = re.compile(r'\b(?:call|invoke)\b.*?\s(?:@("(?:[^"\\]|\\.)*"|[\w.$-]+)|(%[\w.$-]+))\(')


def call_graph(ir):
    """-> ({function: set(direct callees)}, {function: has indirect call})."""
    g, ind, cur = {}, {}, None
    for line in ir.split('\n'):
        if line.startswith('define '):
            m = DEF_RE.match(line)
            cur = m.group(2).strip('"') if m else None
            if cur:
                g[cur] = set(); ind[cur] = False
        elif line.startswith('}'):
            cur = None
        elif cur and ('call ' in line or 'invoke ' in line):
            m = CALL_RE.search(line)
            if m:
                if m.group(1):
                    g[cur].add(m.group(1).strip('"'))
                else:
                    ind[cur] = True
    return g, ind


def rank_blockers(work, pipeline, ir0, ir, defs, declared, out, rowinfo, limit):
    """REPORT ONLY (nothing in the table derives from it): which callees leaving the module keep the most pointer slots at
    e / y. For every row the externals reachable through defined functions are collected (call graph of the optimised
    module); the `limit` best-reaching ones are then what-if'd: its declaration gets nocapture+readonly on every pointer
    parameter, the pipeline is rerun, and the slots (escape e -> n, writes y -> n) that turn proven are counted.
    The pseudo entry <indirect> removes all calls through pointers (host-API table). -> [(name, flipped slots, rows reaching)]
    (see the baseline comment below: the count is the slots that callee ALONE keeps blocked)"""
    g, ind = call_graph(ir)
    blocked = {}
    for key, (letters, va, wl_) in out.items():
        sym = rowinfo[key]
        ps = defs[sym]['params']
        b = sum(1 for i, p in enumerate(ps) if p['type'] == 'ptr' and (letters[i] == 'e' or wl_[i] == 'y')) + (1 if va else 0)
        if b:
            blocked[key] = b
    reach_rows, score = {}, {}
    for key, b in blocked.items():
        seen, todo, ext, has_ind = set(), [rowinfo[key]], set(), False
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            has_ind = has_ind or ind.get(f, False)
            for c in g.get(f, ()):
                if c in g:
                    todo.append(c)
                elif not c.startswith('llvm.'):
                    ext.add(c)
        if has_ind:
            ext.add('<indirect>')
        for c in ext:
            reach_rows[c] = reach_rows.get(c, 0) + 1
            score[c] = score.get(c, 0) + blocked[key]
    cands = sorted(score, key=lambda c: (-score[c], c))
    cands = [c for c in cands if c == '<indirect>'] + [c for c in cands if c != '<indirect>'][:limit]

    def blocked_slots(vd):
        n = 0
        for key, (letters, va, wl_) in out.items():
            v = vd.get(rowinfo[key])
            if not v or len(v['params']) != len(letters):
                return None
            for i, p in enumerate(v['params']):
                if defs[rowinfo[key]]['params'][i]['type'] == 'ptr':
                    n += (not p['nocapture']) + (not p['readonly'])
        return n
    # baseline: EVERY external declaration permissive (nocapture, readonly on each pointer parameter) and no indirect
    # calls; X is then made restrictive again (its declaration as the module has it, indirect calls kept for
    # <indirect>): the increase in blocked slots (escape not n + writes not n) is what X alone blocks.
    orig, perm = {}, {}
    for m in DECL_RE.finditer(ir0):
        nm = m.group(1).strip('"')
        if nm.startswith('llvm.'):
            continue
        e = ir0.index('\n', m.start())
        line = ir0[m.start():e]
        mm = re.search(r'@(?:"(?:[^"\\]|\\.)*"|[\w.$-]+)\(', line)
        a_, b_, ps = declare_params(line, mm.end())
        ps = [('ptr nocapture ' + ('' if 'writeonly' in p else 'readonly ') + p[3:].lstrip()) if p.startswith('ptr') and 'nocapture' not in p and 'readonly' not in p else p for p in ps]
        orig[nm] = line
        perm[nm] = line[:a_] + ', '.join(ps) + line[b_:]
    base = ir0
    for nm in orig:
        base = base.replace(orig[nm], perm[nm], 1)
    vd0 = opt_variant(work, 'base', drop_indirect(base), pipeline)
    b0 = blocked_slots(vd0) if vd0 else None
    res = []
    if b0 is None:
        return res
    for c in cands:
        vir = base if c == '<indirect>' else drop_indirect(base.replace(perm[c], orig[c], 1)) if c in orig else None
        if vir is None:
            continue
        vd = opt_variant(work, 'b%d' % len(res), vir, pipeline)
        bx = blocked_slots(vd) if vd else None
        res.append((c, (bx - b0) if bx is not None else -1, reach_rows.get(c, 0)))
    res.sort(key=lambda t: (-t[1], -t[2], t[0]))
    return res


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bridge', required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--report', required=True)
    ap.add_argument('--work')
    ap.add_argument('--backend', choices=('horizon', 'guest', 'posix'), default='horizon',
                    help='horizon: os_horizon.c + libnx (default); guest: platform/guest/os_guest.c (what toolchain/game/build.sh '
                         'links: libnx is reached only through the FnvnxHostApi table); posix: host backend (fixture test)')
    ap.add_argument('--newlib', default=os.environ.get('FNVNX_NEWLIB', '/home/user/gd/newlib/newlib/libc'),
                    help='newlib libc directory (newlib/libc of the patched 4.6.0.20260123 tree) for the aarch64 backends')
    ap.add_argument('--libnx', default=os.environ.get('FNVNX_LIBNX', '/home/user/gd/libnx'))
    ap.add_argument('--pipeline', default=PIPELINE)
    ap.add_argument('--src', action='append', help='explicit source (testing); replaces the platform source set')
    ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--contract', help='tsv (symbol, param, attrs, source) of DECLARED contracts for external declarations '
                                       '(param 0-based; attrs among nocapture, readonly, readnone); fails if a symbol is defined in the module')
    ap.add_argument('--tc', default=os.environ.get('FNVNX_TC', '/home/user/gd/toolchain/prefix'),
                    help='guest backend: toolchain prefix (newlib + libstdc++ headers of aarch64-none-elf)')
    ap.add_argument('--libnx-inc', default=os.environ.get('LIBNX_INC', '/home/user/gd/toolchain/dkp/libnx/include'),
                    help='guest backend: libnx include directory (sockets declarations)')
    ap.add_argument('--triple', default=GUEST_TRIPLE, help='guest backend: clang target triple (see guest_product)')
    ap.add_argument('--blockers', type=int, default=25, help='external callees to what-if (report only); 0 = none')
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    pipeline = a.pipeline
    work = os.path.abspath(a.work) if a.work else tempfile.mkdtemp(prefix='import_escape_')
    os.makedirs(work, exist_ok=True)

    libnx_commit = 'n/a'
    svcs = set()
    if a.src:
        flags = ['-O1', '-fshort-wchar', '-w']
        jobs = [(os.path.abspath(s), []) for s in sorted(a.src)]
    elif a.backend == 'posix':
        flags = host_flags(root)
        jobs = find_sources(root, 'posix')
    elif a.backend == 'guest':
        flags = []
        jobs, target_desc = guest_product(root, os.path.abspath(a.tc), os.path.abspath(a.libnx_inc), a.triple)
    else:
        nl = os.path.abspath(a.newlib)
        if not os.path.isdir(nl + '/include'):
            die('newlib headers not found at %s/include (need the patched newlib 4.6.0.20260123 libc dir): missing stdio.h, '
                'string.h, errno.h, dirent.h, sys/lock.h, sys/iosupport.h ... all newlib headers' % nl)
        flags = target_flags(root, nl, a.backend)
        jobs = find_sources(root, a.backend)
        if a.backend == 'horizon':
            lx = os.path.abspath(a.libnx)
            r = run(['git', '-C', lx, 'rev-parse', 'HEAD'])
            libnx_commit = r.stdout.strip() if r.returncode == 0 else 'unknown'
            svcs = svc_names(lx)
            jobs = [(s, f + ['-I' + lx + '/nx/include']) for s, f in jobs]
            libnx_jobs = [(s, ['-I' + lx + '/nx/include', '-I' + lx + '/nx/include/switch', '-I' + lx + '/nx/external/bsd/include', '-D__SWITCH__',
                               '-DLIBNX_NO_DEPRECATION', '-fPIC']) for s in libnx_sources(lx)]
        else:
            libnx_jobs = []
    if a.backend in ('posix', 'guest') or a.src:
        libnx_jobs = []
    if not jobs:
        die('no sources found under ' + root)

    bcs, failed, missing = [], [], set()
    with cf.ThreadPoolExecutor(max(1, min(a.jobs, 3))) as ex:
        for src, bc, err in ex.map(lambda j: compile_one((j[0], flags + j[1]), work), jobs + libnx_jobs):
            if bc:
                bcs.append(bc)
            else:
                failed.append((src, tail(err)))
                missing.update(re.findall(r"fatal error: '([^']+)' file not found", err))
    bcs.sort()
    if not bcs:
        die('nothing compiled: ' + str(failed[:1]))
    if failed and a.backend == 'guest' and not a.src:
        # the product set is compiled as a whole (build_guest.sh: no source is left out); a partial module would turn
        # defined callees into unknown ones (sound but useless) and drop rows, so it is an error, not a report
        die('%d product sources failed to compile, first: %s: %s' % (len(failed), failed[0][0], failed[0][1]))

    linked = os.path.join(work, 'linked.bc')
    r = run([LINK] + bcs + ['-o', linked])
    if r.returncode:
        die('llvm-link failed: ' + tail(r.stderr))
    r = run([DIS, linked, '-o', '-'])
    if r.returncode:
        die('llvm-dis failed: ' + tail(r.stderr))
    ir0, n_null = hide_null_tests(strip_volatile(r.stdout))
    contracts = read_contracts(a.contract) if a.contract else []
    if contracts:
        ir0 = apply_contracts(ir0, contracts)
    pre_ll = os.path.join(work, 'linked_novol.ll')
    open(pre_ll, 'w').write(ir0)
    opt_ll = os.path.join(work, 'linked_opt.ll')
    r = run([OPT, '-passes=' + pipeline, pre_ll, '-S', '-o', opt_ll])
    if r.returncode:
        die('opt failed: ' + tail(r.stderr))
    ir = open(opt_ll).read()
    defs, declared = parse_defs(ir)
    svc_called = calls_to(ir, svcs & declared) if svcs else []

    rows = read_bridge(a.bridge)
    out, rep = {}, []
    cands = []   # rows needing the returned-wrapper check
    rowinfo = {}
    for row in rows:
        dll, name, conv, args, sym = row['dll'], row['name'], row['conv'], row['args'], row['native_symbol']
        key = (dll, name)
        if conv == 'data':
            rep.append((dll, name, 'no definition', 'data row')); continue
        if not sym or sym.startswith('fnvl_'):
            rep.append((dll, name, 'no definition', 'implemented elsewhere (%s)' % (sym or 'no symbol'))); continue
        d = defs.get(sym)
        if d is None:
            rep.append((dll, name, 'no definition', 'symbol %s %s' % (sym, 'only declared' if sym in declared else 'not in module'))); continue
        if d['internal']:
            rep.append((dll, name, 'no definition', 'symbol %s has internal linkage' % sym)); continue
        ps = d['params']
        if any(p['byval'] or p['type'] == 'aggregate' for p in ps):
            rep.append((dll, name, 'parameter mismatch', 'bridge args %s vs %d LLVM params with byval/sret/aggregate' % (args, len(ps)))); continue
        if len(ps) != len(args):
            rep.append((dll, name, 'parameter mismatch', 'bridge args %s (%d) vs our %d params' % (args, len(args), len(ps)))); continue
        bad = [i for i, (l, p) in enumerate(zip(args, ps)) if not kind_ok(l, p['type'])]
        if bad:
            rep.append((dll, name, 'parameter mismatch', 'bridge args %s vs param kinds %s (slot %d)' %
                        (args, ','.join(p['type'] for p in ps), bad[0]))); continue
        if (conv == 'vararg') != d['vararg']:
            rep.append((dll, name, 'parameter mismatch', 'bridge conv %s but our prototype %svariadic' % (conv, 'is ' if d['vararg'] else 'is not '))); continue
        letters, wletters = [], []
        for i, p in enumerate(ps):
            wletters.append('n' if p['type'] != 'ptr' or p['readonly'] else 'y')
            if p['type'] != 'ptr':
                letters.append('n')
            elif p['nocapture']:
                letters.append('n')
            elif p['returned']:
                letters.append('?')
                cands.append((key, sym, i))
            else:
                letters.append('e')
        out[key] = [letters, d['vararg'], wletters]
        rowinfo[key] = sym

    # ---- returned -> r only if a discarding wrapper proves nocapture
    r_note = ''
    if cands:
        funcs = sorted({sym for _, sym, _ in cands})
        wl = []
        for sym in funcs:
            d = defs[sym]
            tys = [p['type'] for p in d['params']]
            ret = ret_type_of(d['pre'])
            decl = 'declare %s @%s(%s)' % (ret, sym, ', '.join(tys))
            names = ['%%a%d' % i for i in range(len(tys))]
            wl.append(decl)
            wl.append('define void @fnvw_%s(%s) noinline nounwind {\n  %s call %s @%s(%s)\n  ret void\n}\n' % (
                sym, ', '.join('%s %s' % (t, n) for t, n in zip(tys, names)),
                '' if ret == 'void' else '%r =', ret, sym, ', '.join('%s %s' % (t, n) for t, n in zip(tys, names))))
        wll = os.path.join(work, 'wrappers.ll')
        wbc = os.path.join(work, 'wrappers.bc')
        open(wll, 'w').write('\n'.join(wl))
        okw = run(['llvm-as-18', wll, '-o', wbc]).returncode == 0
        wdefs = {}
        if okw:
            optbc = os.path.join(work, 'linked_opt.bc')
            run(['llvm-as-18', opt_ll, '-o', optbc])
            comb = os.path.join(work, 'combined.bc')
            if run([LINK, optbc, wbc, '-o', comb]).returncode == 0:
                comb_ll = os.path.join(work, 'combined_opt.ll')
                if run([OPT, '-passes=' + pipeline, comb, '-S', '-o', comb_ll]).returncode == 0:
                    wdefs, _ = parse_defs(open(comb_ll).read())
        for key, sym, i in cands:
            w = wdefs.get('fnvw_' + sym)
            letter = 'e'
            if w and len(w['params']) == len(defs[sym]['params']) and w['params'][i]['nocapture']:
                letter = 'r'
            out[key][0][i] = letter
        if not wdefs:
            r_note = 'returned-wrapper stage failed: all returned-only parameters emitted as e'

    # ---- what-if variants: which e rows are e only because of svc / indirect (host-API table) calls
    attrib = {}
    nind = len(re.findall(r'\bcall\b[^\n@]*?%[\w.$-]+\(', ir0))
    if a.backend != 'posix' and not a.src:
        variants = {}
        if svcs:
            variants['svc'] = svc_nocapture(ir0, svcs)
        variants['host'] = drop_indirect(ir0)
        if svcs:
            variants['both'] = drop_indirect(variants['svc'])
        for tag, vir in variants.items():
            vd = opt_variant(work, tag, vir, pipeline)
            attrib[tag] = set()
            if vd is None:
                continue
            for key, (letters, va, wl_) in out.items():
                es = [i for i, l in enumerate(letters) if l == 'e']
                if not es:
                    continue
                v = vd.get(rowinfo[key])
                if v and len(v['params']) == len(letters) and all(v['params'][i]['nocapture'] for i in es):
                    attrib[tag].add(key)

    blockers = []
    if a.backend != 'posix' and not a.src and a.blockers:
        blockers = rank_blockers(work, pipeline, ir0, ir, defs, declared, out, rowinfo, a.blockers)

    lines = []
    for (dll, name), (letters, va, wl_) in sorted(out.items()):
        lines.append('%s\t%s\t%s\t%s' % (dll, name, ''.join(letters) + ('*e' if va else ''), ''.join(wl_) + ('*y' if va else '')))
    nptr = sum(1 for k, (l, v, w) in out.items() for i, p in enumerate(defs[rowinfo[k]]['params']) if p['type'] == 'ptr')
    with open(a.out, 'w') as f:
        f.write(HEADER)
        f.write('# backend: %s%s\n' % (a.backend, {'horizon': ' (platform_host/os/os_horizon.c + libnx %s, target aarch64-none-elf LP64, newlib 4.6.0.20260123 headers)' % libnx_commit,
                                                 'guest': ' (product source set of platform/build_guest.sh + the guest runtime toolchain/guest/{runtime,threads,fs,section_guest}.c, tm.cpp; target %s; libnx only through FnvnxHostApi)' % (target_desc if a.backend == 'guest' and not a.src else ''),
                                                 'posix': ' (platform_host/os/os_posix.c, host x86-64)'}[a.backend] if not a.src else ' (explicit sources)'))
        f.write('# libnx commit: %s\n' % libnx_commit)
        f.write('# pipeline: clang-18 -O1 per file, llvm-link, volatile-clear, null-test-hide (%d tests), opt-18 -passes=\'%s\'\n' % (n_null, pipeline))
        for sym, idx, attrs, src in contracts:
            f.write('# contract: %s param %d %s (source: %s)\n' % (sym, idx, ','.join(attrs), src))
        f.write('dll\tname\tescape\twrites\n')
        f.write('\n'.join(lines) + ('\n' if lines else ''))
    with open(a.report, 'w') as f:
        f.write('# import_escape report: rows omitted from import_escape.tsv (backend %s)\n' % a.backend)
        f.write('# rows emitted: %d; pointer slots among them: %d (escape n/r/e and writes n/y: see the summary lines below)\n' % (len(lines), nptr))
        ne = sum(1 for k, (l, v, w) in out.items() for i, c in enumerate(l) if defs[rowinfo[k]]['params'][i]['type'] == 'ptr' and c == 'n')
        nr = sum(1 for k, (l, v, w) in out.items() for i, c in enumerate(l) if defs[rowinfo[k]]['params'][i]['type'] == 'ptr' and c == 'r')
        nw = sum(1 for k, (l, v, w) in out.items() for i, c in enumerate(w) if defs[rowinfo[k]]['params'][i]['type'] == 'ptr' and c == 'n')
        f.write('# pointer slots: %d; escape n: %d, r: %d, e: %d; writes n: %d, y: %d\n' % (nptr, ne, nr, nptr - ne - nr, nw, nptr - nw))
        for sym, idx, attrs, src in contracts:
            f.write('# contract %s param %d %s: %s\n' % (sym, idx, ','.join(attrs), 'applied' if sym in contract_used else 'declaration not in the module (unused)'))
        for s_, err in sorted(failed):
            f.write('# compile failed: %s: %s\n' % (os.path.relpath(s_, '/'), err.replace('\t', ' ')))
        if missing:
            f.write('# missing headers: %s\n' % ' '.join(sorted(missing)))
        if r_note:
            f.write('# ' + r_note + '\n')
        if a.backend != 'posix' and not a.src:
            f.write('# indirect call sites in the linked module (host-API table etc.): %d\n' % nind)
            f.write('# svc wrappers called (assembly, no bitcode; pointer args stay e): %d: %s\n' % (len(svc_called), ' '.join(svc_called)))
            for tag, what in (('svc', 'rows whose every e slot becomes n if svc wrappers were nocapture'),
                              ('host', 'rows whose every e slot becomes n if indirect (host-API) calls did not capture'),
                              ('both', 'rows whose every e slot becomes n under both what-ifs')):
                if tag in attrib:
                    f.write('# what-if %s: %d %s: %s\n' % (tag, len(attrib[tag]), what, ' '.join(sorted(n for _, n in attrib[tag]))))
        if blockers:
            f.write('# blocking external callees (REPORT ONLY, no fact derives from it): callee, slots (escape not n + writes not n) kept blocked by that callee ALONE (every other external declared\n')
            f.write('#   nocapture+readonly, no indirect calls, vs. this one as the module declares it), and the rows reaching it\n')
            for name_, flips, reach in blockers:
                f.write('# blocker\t%s\t%d\t%d\n' % (name_, flips, reach))
        f.write('dll\tname\treason\tdetail\n')
        for t in sorted(rep):
            f.write('\t'.join(t) + '\n')
    sys.stderr.write('emitted %d rows, omitted %d, compile failures %d, work dir %s\n' % (len(lines), len(rep), len(failed), work))


if __name__ == '__main__':
    main()
