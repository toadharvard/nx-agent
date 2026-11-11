# l5-platform-w9-002: копия загрузчика L5 удалена

Удалено (git rm): loader/src (trap.c, codeptr.c, deldtor.c, image.c, imports.c, pe.c, sha256.c, internal.h), loader/host,
loader/bench, loader/Makefile, loader/.gitignore, include/fnvnx_identity.h, tools/{mkprobes,scan_codeptrs}.py,
tests/s3 целиком (loadcheck, fuzz_loader, gen_synth_pe), tests/v1/{fuzz_soi.c,test_loader_soi.py}; из tools/pipeline/build.py
убран suite s3; из tests/nx1 check_fs/check_threads убраны пути loader/src|host; из toolchain/game/build.sh убраны
компиляция loader/src и case для него. trap.c (trap_value) удалён.
Резервирование адресного пространства (startup/host/reserve.c, reserve_nx.c, fnvnx_reserve.h; os_vm_reserve в platform/os) не тронуто.

Оставлено, потому что нужно платформе или другим слоям (не L5-файлы, байт-в-байт копии заголовков L1, ветка claude/fnv-l1-contract):
- loader/include/{fnvnx_importkey,fnvnx_start,fnvnx_loader,fnvnx_modules,fnvnx_tls,fnvnx_reach}.h — интерфейс L1; platform/boundary/boundary_guest.cpp
  (fnvnx_import_key, fnvnx_start_proc, fnvnx_thread_block_*, fnvnx_module_*, fnvnx_modules_*), platform/win32/reach.h (fnvnx_reach_violation).
  Удалить копию, когда ветка L1 попадёт в транк (reach.h у L1 уже новее: fnvnx_wine_fixme).
- loader/tools/{fnvnx_pe,fnvnx_formats,check_hints,mkdataprobes}.py и loader/synth/* — python-инструменты, их импортируют tools/link, tools/difffuzz, tests/s13, s14, s5, build.py (synth_image). Принадлежат L1/L2/L3.

Ждёт интерфейс L1:
- toolchain/game/boot.cpp использует старый C API (fnvnx_pe_open, fnvnx_image_map, fnvnx_imports_bind, fnvnx_trap_value, fnvnx_image_tls_*) —
  у L1 это fnvnx_start.h/fnvnx_modules.h; сборка игры (toolchain/game/build.sh) не линкуется, пока boot.cpp не переписан на L1 (и trap_value не нужен).
- tools/pipeline/guest_link.py: блок loader/src охраняется isdir, ничего не собирает.
