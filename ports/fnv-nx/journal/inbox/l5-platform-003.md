## J-XXXX: L5 PLATFORM — манифест с D3DX, третья партия импортов (2026-11-10)

Приёмка — прогон лида: `tests/s4` **262 теста / 123 129 проверок / 0 провалов** (было 240); `tests/s7` 30 тестов / 0.

Манифест (fnv-nx-build `l5-platform/manifest`, инструмент `platform/manifest/pemanifest.py`):
- замыкание: 7 модулей (exe, binkw32, libvorbisfile, libvorbis, d3dx9_38 — статические; atimgpud, d3dx9_31 —
  по константному имени), 1 041 слот, 440 уникальных системных импортов (kernel32 172, msvcrt 103, user32 52,
  gdi32 37, wsock32 21, winmm 19, galaxywrp 11 по контракту Steamworks, advapi32 7, ole32 7, прочие 11);
- графы L1 (надмножество, до codemap L3 манифест не режется): d3dx9_38 — достижим 121 импорт из 208, d3dx9_31 —
  81 из 177; шрифтовой GDI недостижим в обеих; сверх таблиц импорта: `d3d9.dll!Direct3DShaderValidatorCreate9`,
  `d3d9.dll!DebugSetMute` (L6).

| Задача (файл-владелец) | До → после | Что закрыто |
|---|---|---|
| `win32/window.cpp` дерево окон | 2 302 → 0 | классы и атомы, дерево под рабочим столом, прямоугольники по правилу AdjustWindowRectEx, SetWindowPos, состояния ShowWindow, последовательности создания и уничтожения |
| `win32/gdi32.cpp` (новый) | не линковалось → 0 | таблица объектов GDI, DIB-секции 1-32 бит, StretchBlt: любая из 256 ROP3 по таблице истинности, 4 режима растяжения, GetDIBits, GetDeviceCaps |
| `audio/waveout.cpp` (новый) | s7: 22 → 30 тестов | устройство waveOut на общем микшере, жизненный цикл заголовков, 4 вида обратного вызова |

Остатки в очереди:
- waveOut: подача через опрос раз в 2 мс с недосчётом при длинном рендере — заменить вытягиванием из микшера;
  `timeBeginPeriod`/`timeEndPeriod` — константа; `CALLBACK_THREAD` без `PostThreadMessage`;
- GDI: `DIB_PAL_COLORS` в `GetDIBits`, `BITMAPCOREHEADER`, `fErase` в `BeginPaint`;
- окна: системные классы (Button, Edit…) без поведения элементов управления; очередь сообщений, хуки,
  `TranslateMessage` — следующая задача;
- тесты s4 не линкуют `platform/audio` (экспорты waveOut объявлены weak) — свести наборы s4 и s7.
