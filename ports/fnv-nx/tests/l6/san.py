"""san.py: the one switch that runs the l6 tests under AddressSanitizer + UndefinedBehaviorSanitizer.

    NX_SAN=asan,ubsan python3 -I tests/l6/<suite>/test_<suite>.py --no-mutations      (unset or empty: the tests run exactly as before)

Optional:  NX_SAN_LOG=<dir>      every sanitizer report goes to <dir>/{asan,ubsan}.<pid> (the tests capture stderr; the files survive)
           NX_SAN_RECOVER=1      discovery mode: reports do not stop the process (-fsanitize-recover=all, halt_on_error=0), so one run lists all findings
           NX_SAN_LSAN_SUPP=<f>  LeakSanitizer suppression file (only for frames wholly inside the Mesa/EGL driver .so files; see SANITIZERS.md)

A test calls san.install() once, before its first subprocess: every gcc/g++ command it then runs gets flags(), every child process gets env().
A test that loads a sanitized shared object into its own python process (ctypes) calls san.install(preload=True): the python process re-executes itself once with the ASan runtime preloaded.
"""
import os, subprocess, sys

_modes = {m for m in os.environ.get("NX_SAN", "").replace(" ", "").lower().split(",") if m}
_unknown = _modes - {"asan", "ubsan"}
if _unknown: sys.exit("san: NX_SAN has unknown mode(s) %s (known: asan, ubsan)" % ",".join(sorted(_unknown)))
_recover = os.environ.get("NX_SAN_RECOVER") == "1"


def active(): return bool(_modes)


def flags():
    """the extra compiler/linker flags (empty without NX_SAN)"""
    if not _modes: return []
    kinds = ",".join(k for k, m in (("address", "asan"), ("undefined", "ubsan")) if m in _modes)
    f = ["-fsanitize=" + kinds, "-fno-omit-frame-pointer", "-g"]
    if "ubsan" in _modes: f.append("-fsanitize-recover=undefined" if _recover else "-fno-sanitize-recover=undefined")
    if "asan" in _modes and _recover: f.append("-fsanitize-recover=address")
    return f


def env(base=None):
    """base (default os.environ) plus the sanitizer runtime options (unchanged without NX_SAN)"""
    e = dict(os.environ if base is None else base)
    if not _modes: return e
    log = os.environ.get("NX_SAN_LOG")
    halt = "0" if _recover else "1"
    a = "detect_leaks=1:abort_on_error=0:halt_on_error=%s:detect_stack_use_after_return=1:allocator_may_return_null=1:quarantine_size_mb=16:strict_string_checks=1:check_initialization_order=1" % halt
    u = "print_stacktrace=1:halt_on_error=%s" % halt
    if log: os.makedirs(log, exist_ok=True); a += ":log_path=" + os.path.join(log, "asan"); u += ":log_path=" + os.path.join(log, "ubsan")
    pre = e.pop("NX_SAN_PRELOADED", None)   # the preload is for this python process only, not for its children (compilers, servers)
    if pre and e.get("LD_PRELOAD", "").startswith(pre): e["LD_PRELOAD"] = e["LD_PRELOAD"][len(pre):].lstrip(":")
    if not e.get("LD_PRELOAD", "x"): del e["LD_PRELOAD"]
    e["ASAN_OPTIONS"] = a; e["UBSAN_OPTIONS"] = u
    supp = os.environ.get("NX_SAN_LSAN_SUPP")
    if supp: e["LSAN_OPTIONS"] = "suppressions=" + supp + ":print_suppressions=0"
    return e


_COMPILERS = {"gcc", "g++", "cc", "c++"}
_NOSAN = ("-E", "-M", "-MM", "-fanalyzer", "-print-file-name=libasan.so")


def _wrap(args):
    if isinstance(args, (list, tuple)) and args and os.path.basename(str(args[0])) in _COMPILERS and not any(a in _NOSAN or str(a).startswith("-print-") for a in args[1:]):
        return [args[0]] + flags() + list(args[1:])
    return args


def install(preload=False):
    """hook subprocess.Popen (subprocess.run/check_output use it): compiler argv gets flags(), the child env gets env()"""
    if not _modes: return
    if preload and "NX_SAN_PRELOADED" not in os.environ:
        if "asan" not in _modes: sys.exit("san: a ctypes host needs NX_SAN to include asan")
        lib = subprocess.run(["gcc", "-print-file-name=libasan.so"], capture_output=True, text=True).stdout.strip()
        pre = os.path.realpath(lib)
        if "ubsan" in _modes: pre += ":" + os.path.realpath(subprocess.run(["gcc", "-print-file-name=libubsan.so"], capture_output=True, text=True).stdout.strip())
        e = env(); e["LD_PRELOAD"] = pre; e["NX_SAN_PRELOADED"] = pre; e["ASAN_OPTIONS"] += ":verify_asan_link_order=0"
        os.execve(sys.executable, sys.orig_argv, e)
    if getattr(subprocess.Popen, "_nx_san", False): return
    orig = subprocess.Popen.__init__

    def init(self, args, *a, **kw):
        args = _wrap(args)
        if kw.get("env") is None: kw["env"] = env()
        else: kw["env"] = env(kw["env"])
        orig(self, args, *a, **kw)
    subprocess.Popen.__init__ = init; subprocess.Popen._nx_san = True
