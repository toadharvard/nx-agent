# L1 CONTRACT 012: четвёртый строгий линк (2026-11-11)

Красный: неопределённых 0 (слабых 0), дубликатов 0, но линкер падает: "read-only segment has dynamic relocations" (-pie -z text). ELF нет. Ветка fnv-nx-build `l1-contract/elf/fb1d0c46eaf7` (7040357).
- Входы: пак l3a f764786639b5 (c193fa78, table_id 7c9d363a), 64 архива без изменений, платформа L5 b03c5a8b (108 источников, свой гейт зелёный), render b23ce2885538 (1922a80, a247bb28), facts 5172f77e, загрузчик 14 членов (fb1d0c46), 71 вход, 203 члена библиотек.
- Причина по таблицам перемещений ELF: read-only секции .rodata с R_AARCH64_P32_ABS32 (константные таблицы с указателями, сборка -fno-pie).
  L5 платформа: 6850 перемещений (в т.ч. FreeType); L6 d3d9: 1197 в 8 членах (gen_d9bridge, gen_d9impl_unk, gen_state, d3d9_exports, dev_chain, draw_state, ffp_dev, res_fvf); наши входы (загрузчик, runtime, lift, пак): 0.
- Проверка: платформа L5 пересобрана локально с -fPIE вместо -fno-pie (ARCH в platform/build_guest.sh, не закоммичено): 0 нарушителей, линк падает только на 8 членах d3d9.
  Владельцы: L5 (build_guest.sh ARCH) и L6 (сборка d3d9: -fPIE, указатели в .data.rel.ro). Ослаблять -z text или менять guest.ld нельзя без решения владельца.
- Время ld.py до отказа линкера 9.6 с, пик RSS 234 МБ. Гейты, сегменты, эмулятор: ELF нет, не запускались.
- Список: ro_abs_relocs.tsv в ветке. Случайно создана ветка fnv-nx-build `x` (копия 1922a80, коммит render bridge); удалить прокси не даёт.
