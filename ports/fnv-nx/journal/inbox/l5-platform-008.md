## J-XXXX: L5 PLATFORM — вторая остановка (2026-11-10, 08:53 UTC), состояние и следующий шаг

Возобновление от оркестратора пришло в 08:48 UTC, остановка владельца (через L4) — в 08:52 UTC. Оба исполнителя
прерваны, их незаконченные файлы закоммичены как WIP этим коммитом. Заметка 007 остаётся в силе, здесь — что
изменилось за 5 минут работы.

### Сдано и запушено между остановками
- `platform/manifest/bridgetable.py` и bridge.tsv со столбцом `bind` (fnv-nx-build `l5-platform/manifest`): sym 287,
  lookup 71, l3 29, data 3, none 50 (49 моих без тела + d3d9!D3DPERF_SetOptions у L6).
- `platform/manifest/import_escape.py` + таблицы (guest 233 строки из 440, horizon 256, posix 296): консервативно и
  неполно; Interlocked*, Enter/LeaveCriticalSection, Read/WriteFile вышли «утекает». L3 сообщено.
- `platform/win32/gdifont.cpp`, этап 1: все 329 строк эталонного дампа шрифтов равны побайтно (2 483 проверки);
  `tests/s4` на голове 9d0a9a5 — 302 теста / 339 317 проверок / 0 провалов (последняя ЗЕЛЁНАЯ голова).
- `fontdump.c` исправлен (fnv-nx-build `tools/fonts` a61d0d8); L4 перезапустил снятие на ферме lg1.

### Незаконченное (WIP в этом коммите; наборы на WIP-голове НЕ прогонялись)
| Задача | Файлы | Где остановлено |
|---|---|---|
| kernel32: противоречия ENV-TRUTH и отсутствующие функции | правки `platform/win32/exports.cpp` и то, что исполнитель успел записать (см. дифф коммита) | в середине; ни один пункт не принят |
| шрифтовой gdi32, этап 2 (ExtTextOut, размеры, размещение, GetOutlineTextMetricsA, CreateDCA, ExtEscape) | — | исполнитель остановлен на чтении кода, файлов не оставил |

### Следующий шаг при возобновлении (по порядку)
0. Противоречия ENV-TRUTH (список в заметке 007, п. 0) и 23 отсутствующие функции kernel32 — прежнее ТЗ, порядок
   A5 → A4 → A2 → A1 → A3.
1. Шрифтовой gdi32, этап 2 — прежнее ТЗ; эталон — новый `prefix/fontdump.txt` из `tools/fonts`.
2. Перегенерировать bridge.tsv (`bridgetable.py … <nm-символы> platform/crt/msvcrt_exports.cpp`), число `none`
   должно упасть с 50; сообщить L3/L1.
3. import_escape на ферме: clang ≥ 21, libnx b156b47, newlib 4.6.0.20260123 с патчем devkitPro, заголовки libstdc++
   тулчейна fnvnx-a64; строка запуска у L4 (отложена им как «новая работа»).
4. `merge claude/fnv-l5-platform` через MERGER после зелёных `tests/s4` и `tests/s7`.

### Что нужно первому полностью переведённому бинарнику от L5
- Один FalloutNV.exe: недостающих тел импортов у L5 нет (240 системных; чужой один — d3d9!D3DPERF_SetOptions).
- exe + binkw32 + libvorbisfile + libvorbis + d3dx9_38: не хватает 45 тел; после этапа 1 шрифтов закрыто 15 из 22
  шрифтовых (остались ExtTextOutA/W, GetCharacterPlacementA/W, GetOutlineTextMetricsA; CreateDCA и ExtEscape нужны
  только atimgpud), kernel32 — 15 + 7 для bink/vorbis, advapi32!RegOpenKeyA.
