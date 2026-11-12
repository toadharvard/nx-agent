# tests/guest_os (L4 item 11 step B; NOT admitted yet)

Test root, class `test`, target guest. Links `ports/fnv-nx/rt/guest` + `ports/fnv-nx/platform` (link.roots), script `link/guest.ld`.
`main.c` is the only unit; it defines `main` and test-local `g_*` globals, nothing the product roots define.

Run on the boot machine: exit code 0 = checks 1..11 held (locks, thread create/start/join, condvar signal and timed wait,
monotonic clock and sleep). The GPU case (the former `rh_gpu_call` test of tests/s4/host_gpu.cpp, 015cc965^): the harness reads
`g_gpu_ret[0..1]` from the ELF symbol and compares them with `Gpu.ext_10(7, g_gpu_args) / ext_10(0, [])` called directly in bootgpu.py,
which proves `nxd3d_gpu_call` returns the backend word unchanged, one call, fn/nargs/args as given, nargs 0 with NULL valid.
