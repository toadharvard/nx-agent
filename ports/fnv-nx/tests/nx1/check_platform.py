#!/usr/bin/env python3
"""NX1 check 7: the console platform around the game: input, audio, suspend/resume, exit, crash log.

    check_platform.py [--no-run]

  hid      the host's input (toolchain/host/host_v3.inc): four pads in the standard style set (handheld, dual and
           single Joy-Con, Pro Controller) with player 1 = handheld or No1, keyboard, mouse, touch, vibration for
           every style; the launcher reads any pad;
  audio    audren: initialised, the renderer started, closed again, the pump on the audio role;
  applet   the applet pump (host_device.inc) starts with the guest, h_main_loop defers to it, its hook covers focus,
           resume, operation mode (docked 1080p / handheld 720p) and exit requests;
  exit     every way out runs cleanup_for_exit (boost off, override removed, log flushed): the guest's exit(), the
           guest returning, a system exit request (then exit(0), so libnx ends with ISelfController::Exit), a refused
           start; svcExitProcess only in the crash handler. The process then returns to the HOME menu: hbmenu
           lives in hbloader's process, which the game process replaced (docs/NRO.md);
  crash    __libnx_exception_handler writes FNVNX_FS_CRASH_LOG through fsFs with its own stack;
  run      tests/nx1/host_device_test.c, built as a static AArch64 Linux program against libnx's headers with the
           libnx calls stubbed and run under qemu-aarch64: override removal, refusals, the crash log's content and
           deletes, the exit request path.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from check_fs import strip_comments  # noqa: E402
from check_nro import Checks  # noqa: E402


def src(rel):
    return strip_comments(open(os.path.join(ROOT, rel), errors="replace").read())


def body(text, fn):
    m = re.search(r'\b' + re.escape(fn) + r'\s*\([^;{]*\)\s*\{', text)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i]


def check(run: bool) -> Checks:
    c = Checks()
    v3, host, dev, nro = (src(p) for p in ("toolchain/host/host_v3.inc", "toolchain/host/host.c",
                                            "toolchain/host/host_device.inc", "toolchain/nro/launcher.c"))
    c.ok("hid.style", re.search(r'padConfigureInput\(\s*4\s*,\s*HidNpadStyleSet_NpadStandard\s*\)', v3) is not None,
         "host_v3: not 4 pads in the standard style set")
    c.ok("hid.handheld", re.search(r'padInitialize\(&g_ipads\[0\],\s*HidNpadIdType_No1,\s*HidNpadIdType_Handheld\)', v3)
         is not None, "player 1 does not include the handheld controller")
    for k in ("hidInitializeKeyboard", "hidInitializeMouse", "hidInitializeTouchScreen", "hidSendVibrationValues"):
        c.ok(f"hid.{k}", k in v3, f"host_v3 lacks {k}")
    c.ok("hid.vibration_styles", all(t in v3 for t in ("NpadHandheld", "NpadJoyDual", "NpadFullKey")),
         "vibration does not cover handheld, dual Joy-Con and Pro")
    c.ok("hid.launcher", "padInitializeAny" in nro, "the launcher reads only one controller style")
    for k in ("audrenInitialize", "audrenStartAudioRenderer", "audrenExit", "audrenWaitFrame"):
        c.ok(f"audio.{k}", k in v3, f"host_v3 lacks {k}")
    c.ok("audio.role", re.search(r'os_thread_create\(pump,[^;]*OS_THREAD_ROLE\(OS_ROLE_AUDIO\)', src("platform/audio/backend.cpp"))
         is not None, "the audio pump thread has no audio role (placement.h row)")

    mainf = body(host, "main")
    c.ok("applet.pump_started", "pump_start()" in mainf, "host main does not start the applet pump")
    c.ok("applet.main_loop", re.search(r'h_main_loop\(void\)\s*\{\s*return\s+g_pump_run', host) is not None,
         "h_main_loop calls appletMainLoop next to the pump")
    hook = body(dev, "applet_hook")
    for k in ("OnFocusState", "OnResume", "OnOperationMode", "OnExitRequest"):
        c.ok(f"applet.hook.{k}", f"AppletHookType_{k}" in hook, f"the applet hook ignores {k}")
    pump = body(dev, "pump_main")
    c.ok("exit.request", "cleanup_for_exit" in pump and "exit(0)" in pump and "svcExitProcess" not in pump,
         "the exit request path does not clean up and exit()")
    c.ok("exit.guest_exit", "cleanup_for_exit" in body(host, "h_exit"), "the guest's exit() skips the cleanup")
    c.ok("exit.guest_return", "cleanup_for_exit" in mainf, "a returning guest skips the cleanup")
    pre = mainf[mainf.find("device_preflight"):mainf.find("device_preflight") + 300]
    c.ok("exit.refused", "remove_launcher_override" in pre, "a refused start leaves the override installed")
    cl = body(dev, "cleanup_for_exit")
    for k in ("ApmCpuBoostMode_Normal", "remove_launcher_override", "fflush"):
        c.ok(f"exit.cleanup.{k}", k in cl, f"cleanup_for_exit lacks {k}")
    other = (host + dev).replace(body(dev, "__libnx_exception_handler"), "")
    c.ok("exit.no_raw_exit", "svcExitProcess" not in other, "svcExitProcess outside the crash handler (skips SelfExit)")
    eh = body(dev, "__libnx_exception_handler")
    c.ok("crash.handler", bool(eh) and "FNVNX_FS_CRASH_LOG" in eh and "fsFileWrite" in eh, "no crash log writer")
    m = re.search(r'__nx_exception_stack\[(0x[0-9a-fA-F]+|\d+)\]', dev)
    c.ok("crash.stack", m is not None and int(m.group(1), 0) >= 0x2000, "exception stack missing or < 8 KiB")
    c.ok("crash.armed", "crash_handler_arm(gbase, gsize)" in mainf, "the crash handler does not know the guest range")

    if run:
        cc, qemu = shutil.which("aarch64-linux-gnu-gcc"), shutil.which("qemu-aarch64-static") or shutil.which("qemu-aarch64")
        libnx = os.path.join(ROOT, "ext", "libnx", "nx", "include")
        if c.ok("run.tools", cc and qemu and os.path.isdir(libnx),
                "needs aarch64-linux-gnu-gcc, qemu-user and the libnx checkout (tools/fetch.sh)"):
            with tempfile.TemporaryDirectory() as t:
                exe = os.path.join(t, "hdt")
                r = subprocess.run([cc, "-static", "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-D__SWITCH__",
                                    "-isystem", os.path.join(HERE, "syntax"), "-I" + os.path.join(ROOT, "toolchain", "include"),
                                    "-I" + libnx, os.path.join(HERE, "host_device_test.c"), "-o", exe],
                                   capture_output=True, text=True, errors="replace")
                if c.ok("run.build", r.returncode == 0, r.stderr.strip()[-300:]):
                    wd = os.path.join(t, "wd")
                    os.makedirs(wd)
                    r = subprocess.run([qemu, exe, wd], capture_output=True, text=True, errors="replace", timeout=120)
                    c.ok("run.host_device", r.returncode == 0,
                         "; ".join(l for l in r.stdout.splitlines() if l.startswith("FAIL"))[:300] or r.stderr[-200:])
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true")
    a = ap.parse_args(argv)
    c = check(not a.no_run)
    c.print()
    print(f"platform: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
