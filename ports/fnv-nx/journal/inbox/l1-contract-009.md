# L1 CONTRACT 009: первый строгий линк на реальных входах (2026-11-11)

Красный: 48 неопределённых имён, слабых 0 (до S2: 66, слабых 19). ELF нет. Ветка fnv-nx-build `l1-contract/elf/d399c60476fc`
(undefined.tsv, undefined_by_owner.tsv, гейт ld, keys/modules).
- (a) наши: 0. (b) платформа L5: 12 сокетов (sock_guest.c ветки L5 не в нашей toolchain/guest) + os_shm_create/free/map/unmap. (c) пак L3a: 32
  (fnvl_* из rt.a/disp.o, fnvnx_lift_enter/imports_init, fnvnx_guest_current/invoke, fnvl_msvcrt_adjust_fdiv). (d) тулчейн: 0.
- Пака `l3a-lift/pack/f764786639b5` в fnv-nx-build нет (есть только trial-0of64, bridge 7d57943b); table_id не сверен. Голова bridge L5: 3086607d.
- hooks.c не добавлен: fnvl_host_tls_slot / fnvl_host_ticks_ns уже определены платформой (platform/boundary/host_hooks.cpp), второе определение даст двойное.
- Ветка L5 расходится с нашей в toolchain/include/fnvnx/hostapi.h и toolchain/guest (sock_guest.c, os_guest.c): слияние за L4.
- Платформа собрана build_guest.sh из ветки L5 (nls Wine 9.0, FreeType Debian, minimp3 по пину; gen.inc как в tests/s4/Makefile: build_guest.sh их не делает).
