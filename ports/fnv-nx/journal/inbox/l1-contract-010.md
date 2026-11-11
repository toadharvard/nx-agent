# L1 CONTRACT 010: второй строгий линк (2026-11-11)

Красный: 140 неопределённых (слабых 0), дубликатов определений 0, ELF нет. Ветка fnv-nx-build `l1-contract/elf/ca09c58e558d` (96e01bf).
- Входы: пак l3a f764786639b5 (750b1c9c, table_id 7c9d363a... = bridge l5-platform/manifest 7b25c82), шард 57 перекачан (99927 из 99927 L_ определены по одному разу),
  платформа L5 bd4256c5 (103 источника, 14541936 B), render f712b9e15d0c (единственная ветка), facts 5172f77e, загрузчик из этого bridge (280 слотов, 0 без значения).
- По владельцам: L6 render 123 (имена без тела в самом libfnvnx-d3d9.a), L5 платформа 16 (12 сокетов: sock_guest.c есть только в toolchain/guest ветки L5;
  os_shm_create/free/map/unmap: объявлены в platform/os/os.h, определены только в os_posix.c), L3a 1 (fnvl_msvcrt_adjust_fdiv: строка bridge _adjust_fdiv, imp_blocked), наши 0, тулчейн 0.
- imp_blocked.tsv: 35 строк (красные id gate disp, сам гейт не запускался: ELF нет).
- hostapi.h (наш vs L5): отличается 1 определение (entry), только у нас 4 (FNVNX_VM_PROT_R/W, FNVNX_PLAT_ACCESS_FAULT_HANDLER, FnvnxFaultFrame), только у L5 23 (18 FNVNX_PLAT_SOCK_*, FIRST/LAST, enum, protect, bits). Раскладки общих структур не менялись.
- toolchain/guest ветки L5 расходится с нашей (os_guest.c, runtime.c, threads.c, crt0.s, build.sh, guest.ld/specs): слияние за L4.
- Время стадии ld.py 1.2 с, пик RSS 94 МБ; сегменты/RELR/RELA/эмулятор: нет ELF, не измерялись.
