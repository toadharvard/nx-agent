# L1 CONTRACT 013: первый guest.elf, строгий линк зелёный (2026-11-11)

Ветка fnv-nx-build `l1-contract/elf/509809b3b809` (192ad206), 3 части по <= 90 МБ + manifest.json. Неопределённых 0, дубликатов 0.
- Входы: пак l3a f764786639b5 head 38a7b53 (table_id 2f20d75e), bridge l5-platform/manifest 634e4af (1552 строки, sha256 = table_id), платформа L5 4f074549 без изменений (-fPIE), render l6 9d06aa4eed1c, facts 5172f77e, загрузчик 14 членов, 64 архива lift без изменений.
- ELF 216702236 Б. Сегменты: R E 0xba05ce8 (.text 0x30aa08 + .text.fnvl 0xb6fb268), R 0x7b5e0c (.rodata 0x3214d0), RW file 0x18b68 / mem 0x2a1660 (.data 0x3b60, .bss 0x278470). RELR 691 слов / 12021 перемещение, RELA 0, веньеров/stub-секций нет (.plt пуст).
- ld.py: 6.2 с, пик RSS 527 МБ. Гейты: ld зелёный (119k), disp universe 101478 / certified 101478 / красных 0 (imp_blocked 0 кодовых; runtime keys not in keys.tsv: 2), pcrel 0 из 1587788, abs 0 из 2485527.
- Эмулятор (158 с, лимит 600): образ exe + 4 статических модуля, imports bound 280 (+208/119/73/96), последняя строка fnvnx-start: init d3dx9_38.dll; затем UnhandledExceptionFilter(0x18100ccc), ExitProcess(0xc000001d). fnvnx-wine-fixme 0, fnvnx-reach 0. Выполнено инструкций 1075895420 (1 поток), host_calls 342. pc и адрес сбоя хостом не печатаются (исключение обработано внутри гостя), символ pc не определён.
- Вехи milestones: все 10 не достигнуты (winmain и др. не в линке: адреса без тел).
- Наше не правили.
