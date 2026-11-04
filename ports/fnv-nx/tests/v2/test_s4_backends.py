#!/usr/bin/env python3
"""V2 check of the S4 device backends (c7bce4c VirtualAlloc/VirtualFree, eab58fd SuspendThread/ResumeThread, and the
other backend commits), through S4's own AArch64 build under qemu (`make -C tests/s4 qemu`), plus the Win32 semantics
V2 compared with Wine's (Windows-compatible) VirtualAlloc.

    python3 tests/v2/test_s4_backends.py     # aarch64 toolchain, qemu-aarch64

  B1  the backend tests virtual_alloc, thread_suspend_running, thread_suspend_self_and_created_suspended,
      thread_suspended_start_and_exit_code pass under qemu; so do the tests of 4b50aa2 (absent services), 4e4ce66
      (winmm mmio: V2 also read that Descend and Ascend skip the pad byte of odd-sized RIFF chunks) and 82321af
      (libcpmt names)
  note 82321af defines _ZNSt9exceptionC2Ev with the MSVC layout under libstdc++'s name; a GCC TU emitting
      libstdc++'s inline copy out of line (-O0) would share the weak name, link order picking one. Latent (-O2)
  note time_counters (Sleep(50) measured by the wall clock) is timing-sensitive under a loaded qemu: reported, not
      counted
  note memory.cpp returns the page-rounded address for MEM_RESERVE|MEM_COMMIT at an address that is not 64 KiB
      aligned and commits only the pages of [addr, addr+size); Windows (Wine 9: VirtualAlloc(0x30001000, 0x1000,
      MEM_RESERVE|MEM_COMMIT) -> 0x30000000, page 0 committed too) returns the 64 KiB base. The game reserves only at
      NULL or 16 MiB-aligned hints (memoryheap, scrapheap, blk_00aa0000_od) and commits with MEM_COMMIT alone, so
      the difference is never reached: not counted.
"""
import os
import re
import subprocess
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
WANT = ('virtual_alloc', 'thread_suspend_running', 'thread_suspend_self_and_created_suspended',
        'thread_suspended_start_and_exit_code',
        'imports_offline_and_steam_policy',                                   # 4b50aa2
        'mmio_custom_procedure_like_the_game', 'mmio_dos_file_chunks', 'mmio_memory_file', 'mmio_write_chunks',  # 4e4ce66
        'msvc_std_names', 'cpplib_exception_layout_and_semantics')            # 82321af


def main():
    r = subprocess.run(['make', '-C', os.path.join(PORT, 'tests', 's4'), 'qemu'], capture_output=True, text=True,
                       errors='replace', timeout=3000)
    st = dict(re.findall(r'^(\w+)\s+(ok|FAIL)\s*$', r.stdout, re.M))
    if not st:
        print('skip: make -C tests/s4 qemu ran no tests (%s)' % (r.stdout + r.stderr).strip()[-200:])
        return 0
    fails = 0
    for t in WANT:
        ok = st.get(t) == 'ok'
        print('%s B1 %s: %s' % ('ok' if ok else 'FAIL', t, st.get(t, 'missing')))
        fails += not ok
    print('note time_counters: %s (timing-sensitive)' % st.get('time_counters', 'missing'))
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
