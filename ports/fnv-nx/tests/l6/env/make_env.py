#!/usr/bin/env python3
"""make_env.py: record the ONE reference environment (Wine 9.0 + Mesa llvmpipe + Xvfb screen) in both profiles.
 build envprobe2.exe (mingw) + recorder d3d9.dll; per profile: Xvfb WxHx24, prefix with HKCU\\Software\\Wine\\Direct3D\\VideoMemorySize=960, run envprobe2 under Wine with the
 recorder TWICE -> the two answer files must be identical (determinism); the recorder's D9S1 stream is decoded and cross-checked against the program's answers
 (same methods in the same order, same return values, env-class marking); writes render/d3d9/env/env_<profile>.tsv and ENV_PIN.txt.
env: D9ENV_WORK (default /home/user/work/l6/env).  usage: make_env.py [handheld] [docked]"""
import hashlib, json, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "..", "..", "render", "d3d9", "env"), os.path.join(ROOT, "tools", "compare"), os.path.join(HERE, "..", "wrap")]
import envq, replay, d9s1_read as R  # noqa: E402
W = os.environ.get("D9ENV_WORK", "/home/user/work/l6/env"); OUT = os.path.join(ROOT, "render", "d3d9", "env")
SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
PROFILES = {"handheld": (1280, 720), "docked": (1920, 1080)}
VIDMEM_MB = 960
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(ok, what):
    if not ok: fails.append(what); print("FAIL", what)


def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()


def build():
    os.makedirs(W + "/gen", exist_ok=True); os.makedirs(W + "/run", exist_ok=True)
    open(W + "/gen/envq.gen.h", "w").write(envq.emit()); open(W + "/gen/envfmt.gen.h", "w").write(envq.emit_fmt())
    r = sh(["i686-w64-mingw32-gcc", "-O1", "-w", "-I", W + "/gen", "-I", HERE, "-Wl,--no-insert-timestamp", "-o", W + "/run/envprobe2.exe", HERE + "/envprobe2.c", "-ld3d9", "-luser32", "-static-libgcc"])
    check(r.returncode == 0, "envprobe2 build: " + r.stderr[-500:])
    r = sh([os.path.join(ROOT, "render/tools/rec/build.sh"), W + "/rec"]); check(r.returncode == 0, "recorder build: " + r.stderr[-300:])
    shutil.copy(W + "/rec/d3d9.dll", W + "/run/d3d9.dll")


def prefix(env):
    pfx = W + "/pfx"
    if not os.path.isfile(pfx + "/system.reg"): sh(["wineboot", "-i"], env=env, timeout=900); sh(["wineserver", "-w"], env=env, timeout=900)
    sh(["wine", "reg", "add", r"HKCU\Software\Wine\Direct3D", "/v", "VideoMemorySize", "/t", "REG_SZ", "/d", str(VIDMEM_MB), "/f"], env=env, timeout=300); sh(["wineserver", "-w"], env=env)
    q = sh(["wine", "reg", "query", r"HKCU\Software\Wine", "/s"], env=env, timeout=300).stdout; sh(["wineserver", "-w"], env=env)
    return [l.strip() for l in q.splitlines() if l.strip()]


def run_once(env, tsv, stream, extra=()):
    e = dict(env, WINEDLLOVERRIDES=env["WINEDLLOVERRIDES"] + ";d3d9=n,b", D9REC_OUT=replay.wine_path(stream))
    r = sh(["wine", W + "/run/envprobe2.exe", tsv] + list(extra), env=e, timeout=1800); sh(["wineserver", "-w"], env=env, timeout=300)
    return r.returncode, r.stderr[-300:]


def xcheck(stream, rows, spec):
    """stream vs the program's rows: same env-method sequence, same return values; every env-class record's method is one of the probed ones."""
    hdr, recs = R.read_stream(open(stream, "rb").read(), spec)
    ops = set(envq.OPS) - {"Direct3DCreate9", "Direct3DCreate9Ex"}
    a = [(r.name, int.from_bytes(v, "little") if isinstance(v, (bytes, bytearray)) else v) for r in recs if (r.iface or "").startswith("IDirect3D9") and "Device" not in r.iface and r.name in ops
         for (l, c, rc, v) in r.items if l == "ret"]
    b = [(m.group(1), int(t[1], 16)) for t in (x.split("\t") for x in rows if not x.startswith("#")) for m in [re.match(r"(\w+)\(", t[0])] if m and m.group(1) in ops]
    nz = lambda l: [(n, int(v != 0) if n == "GetAdapterMonitor" else v) for n, v in l]   # an HMONITOR is a handle: the stream carries the recorder's token, so only NULL / non-NULL is comparable
    a, b = nz(a), nz(b)
    nenv = sum(1 for r in recs for (l, c, rc, v) in r.items if l == "ret" and c == 1)
    return len(recs), len(a), len(b), a == b, nenv


def main():
    spec = json.load(open(SPEC)); want = [p for p in sys.argv[1:]] or list(PROFILES)
    build()
    if fails: return 1
    pin = {}; os.makedirs(OUT, exist_ok=True)
    for name in want:
        w, h = PROFILES[name]; os.environ["S16_DISPLAY"] = ":%d" % (180 + os.getpid() % 40)   # a display number of our own: another session's Xvfb on the same number made the server die and Wine run on a foreign screen
        x = replay.Xvfb(w, h); env = replay.wine_env(W + "/pfx", x.display); lock = replay.prefix_lock(W + "/pfx"); import time; time.sleep(1)
        check(x.p.poll() is None, f"{name}: Xvfb {x.display} did not stay up")
        try:
            reg = prefix(env); res = []
            for k in (1, 2):
                t = f"{W}/run/{name}_{k}.tsv"; s = f"{W}/run/{name}_{k}.d9s"
                for f in (t, s):
                    if os.path.exists(f): os.remove(f)
                rc, err = run_once(env, replay.wine_path(t), s)
                check(rc == 0, f"{name} run {k}: exit {rc} {err}"); res.append((t, s))
            g = subprocess.run(["wine", W + "/run/envprobe2.exe", replay.wine_path(W + "/run/pin.tsv"), "pin"], env=dict(env, WINEDEBUG="+d3d"), capture_output=True, text=True, timeout=300).stderr
            gl = [re.sub(r"^\S+ ", "", l) for l in g.splitlines() if "GL_RENDERER" in l or "GL_VERSION" in l or "GL_VENDOR" in l][:3]
            sh(["wineserver", "-k"], env=env)
        finally:
            x.close(); lock.close()   # the prefix lock is per process: a second profile would block on it forever
        a, b = (open(t, "rb").read() for t, _ in res)
        dmrow = next(l for l in a.decode().splitlines() if l.startswith("GetAdapterDisplayMode(0)")); check(f"Width={w:x} Height={h:x} " in dmrow, f"{name}: display mode is not {w}x{h}: {dmrow}")
        check(a == b, f"{name}: two runs differ (determinism)"); rows = a.decode().splitlines()
        n, ne, nr, same, nenv = xcheck(res[0][1], rows, spec)
        check(same and ne == nr, f"{name}: stream vs answers ({ne} vs {nr} records)")
        print(f"{name}: {len(rows)} rows; run1 == run2: {a == b}; stream {n} records, {ne} env-method records == {nr} answer rows: {same}; env-class returns in stream {nenv}")
        dst = f"{OUT}/env_{name}.tsv"; open(dst, "w").write("#query\tret\touts\n" + "\n".join(rows) + "\n")
        pin[name] = dict(w=w, h=h, rows=len(rows), sha=sha(dst), stream=n, gl=gl, reg=reg)
    if fails: return 1
    wv = sh(["wine", "--version"]).stdout.strip(); dp = sh(["dpkg-query", "-W", "-f", "${Package}:${Architecture} ${Version}\\n", "wine", "wine32:i386", "libwine:i386", "libgl1-mesa-dri:i386",
                                                              "libglx-mesa0:i386", "libllvm20:i386", "xvfb"]).stdout
    # the toolchain that built the two Windows binaries: every installed mingw-w64 package (gcc, g++, binutils, headers, crt) by package and version,
    # and the version lines of the driver and the linker; no line means no pin, so no ENV_PIN (the probe would not be reproducible)
    mg = sh(["dpkg-query", "-W", "-f", "${Package}:${Architecture} ${Version}\\n", "gcc-mingw-w64-*", "g++-mingw-w64-*", "binutils-mingw-w64-*", "mingw-w64-*"]).stdout
    tc = [sh([c, "--version"]).stdout.splitlines()[:1] for c in ("i686-w64-mingw32-gcc", "i686-w64-mingw32-g++", "i686-w64-mingw32-ld")]
    if not mg.strip() or not all(tc): print("make_env: FAILED: the mingw-w64 packages / tool versions cannot be read, the binaries would have no pinned toolchain"); return 1
    bindir = os.environ.get("D9ENV_BIN", W + "/bin"); os.makedirs(bindir, exist_ok=True)   # the two binaries, archived by the caller in fnv-nx-build tools/envprobe-bin
    for b in (W + "/run/envprobe2.exe", W + "/run/d3d9.dll"): shutil.copy(b, os.path.join(bindir, os.path.basename(b)))
    cpu = [l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")][:1]
    L = ["ENV_PIN: the pinned recording environment of render/d3d9/env/env_*.tsv (written by tests/l6/env/make_env.py; do not edit)",
         f"wine --version: {wv}", "packages (dpkg):", *["  " + l for l in dp.splitlines()], f"host cpu (llvmpipe vector width follows it): {cpu[0] if cpu else '?'}",
         "renderer: LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe (Mesa 25.2.8, LLVM 20.1.2 as the GL strings below say); Wine renderer = OpenGL (wined3d default)",
         "display: Xvfb <display> -screen 0 WxHx24 -nolisten tcp -noreset (no window manager, no RANDR mode list beyond the screen: one mode)", f"emulated video memory: HKCU\\Software\\Wine\\Direct3D\\VideoMemorySize = {VIDMEM_MB} (MiB)",
         "build toolchain (dpkg): ", *["  " + l for l in mg.splitlines()], *["  " + x[0] for x in tc],
         f"recorder d3d9.dll sha256 {sha(W + '/rec/d3d9.dll')}", f"envprobe2.exe sha256 {sha(W + '/run/envprobe2.exe')}", "both binaries archived in fnv-nx-build tools/envprobe-bin (the files of D9ENV_BIN: d3d9.dll, envprobe2.exe)", f"query list: {envq.count()} queries (envq.py), {len(envq.FMT)} formats"]
    for name, p in pin.items():
        L += [f"profile {name}: screen {p['w']}x{p['h']}x24, {p['rows']} answer rows, env_{name}.tsv sha256 {p['sha']}, recorded stream {p['stream']} records"] + ["  GL: " + g for g in p["gl"]]
    L += ["registry HKCU\\Software\\Wine (reg query /s, last profile):"] + ["  " + l for l in next(reversed(pin.values()))["reg"][:60]]
    open(OUT + "/ENV_PIN.txt", "w").write("\n".join(L) + "\n"); print("wrote ENV_PIN.txt; FAILED: " + "; ".join(fails) if fails else "make_env: OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
