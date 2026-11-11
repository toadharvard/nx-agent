## J-XXXX: L5 PLATFORM — A11 и противоречия ENV-TRUTH закрыты (2026-11-11)

### Числа (голова de35de52)
- `tests/s4`: 314 тестов / 340 293 проверки / 0 провалов. `tests/s7`: 63 / 2 321 / 0.
- WIP-голова 2a0a07ca не линковалась: 23 функции без тела. Теперь все 23 реализованы.
- bridge.tsv (fnv-nx-build `l5-platform/manifest` 6bfdcbf), 440 строк: sym 322, lookup 71, l3 32, data 3, none 12
  (было none 50). Остаток none: 7 шрифтовых gdi32, CreateFileMappingA, MapViewOfFile, UnmapViewOfFile,
  VirtualProtect, ChangeDisplaySettingsA.

### Что закрыто
| Пункт | Правило |
|---|---|
| F5 консоль | стандартных хэндлов нет: `GetStdHandle` = NULL, консольные вызовы — `ERROR_INVALID_HANDLE`; схема псевдо-хэндлов удалена |
| F6/P1 окружение | блок отсортирован как в Windows, значения из одного источника с `GetSystemInfo` и каталогами |
| P4/P5 | `GetVersionExA`/`GetVersion` 6.1.7601 SP1, `GetSystemDirectoryA`, `GetWindowsDirectoryA` |
| M5 модули | одна таблица модулей; список системных DLL выводится из таблицы экспортов; `GetProcAddress` по имени и ординалу |
| M6 Steam | `SteamAPI_Init`/`IsSteamRunning` = false, аксессоры NULL, список регистраций настоящий |
| P3 исключения | платформа поднимает исключения через `w32_raise` → бэкенд рантайма; `RaiseException`/`RtlUnwind`/`DebugBreak` игры — bind=l3 (`fnvl_os_*`) |
| прочее | `IsProcessorFeaturePresent`, `SetErrorMode`, `IsDBCSLeadByte`, `ExpandEnvironmentStringsA`, `SetConsoleCtrlHandler`, `RegOpenKeyA`, `GetTempPathA`, `GetTempFileNameA/W`, `DeleteFileW`, `MoveFileW`, `FindResourceW`, `GetDateFormatA`, `GetTimeFormatA`, `FatalAppExitA`, `DisableThreadLibraryCalls` |

### Контракты, согласованные с другими лидами
- L1: ключ импорта один (`contract/iat.py`, `fnvnx_import_key`); `GetProcAddress` вернёт его. Поток: `fnvnx_thread_block_create`
  + `fnvnx_lift_enter`. Карта секций — `fnvnx_proc`. Data-строка = адрес ELF-символа (`__badioinfo`, `__pioinfo`, `fnvnx_iob`).
  Загрузчик зовёт `fnvnx_win32_register_image(name, base, size)` (с моей стороны ещё не написана).
- L6: `D3DPERF_SetOptions` (ret v) — его символ; COM-vtable d3d9 — ключами, строки `!<Интерфейс>` в bridge.tsv.
- L4: виртуальные часы VC1 с квантом 100 нс; список зависящих от часов импортов — `manifest/clockdeps.tsv` (c38fb34).

### Класс без правила в плане (сообщено оркестратору и L3a, ответа L3a нет)
Кодовые указатели через границу платформы. Гость → платформа: обратные вызовы (WNDPROC, старт потока, qsort, фильтр
исключений) платформа зовёт как нативные. Предложенное правило: типы-обёртки в заголовках, вызов только через хук
рантайма. До WinMain не блокирует, до главного меню блокирует.

### Открытые пункты (записаны, не чинятся сейчас)
- На хосте `GetSystemInfo` даёт число ядер машины, не 3 (P1); бэкенд Horizon даёт 3, гостевой бэкенд не проверен.
- `DATE_LTRREADING`/`RTLREADING`/`AUTOLAYOUT` принимаются, меток направления не добавляют: правило для en-US не найдено.
- Без рантайма фильтр необработанных исключений получает нулевой CONTEXT.
- `tests/s4/backends/backends.tsv` содержит устаревшую строку `RtlUnwind`.
- `test_plat.cpp` считает `fnvnx_nvapi_not_initialized` «документированным no-op» — разобрать в B2.
- Формат года-месяца: эталонные NLS-данные Wine 9.0 дают «October 2010» без запятой; тест исправлен под данные.

### В работе
W4 шрифтовой gdi32 этап 2; W5 единая карта страниц и отображение файлов; W6 `platform/build_guest.sh` (архив для ld L1).
