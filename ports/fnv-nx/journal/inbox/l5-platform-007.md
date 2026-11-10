## J-XXXX: L5 PLATFORM — остановка по приказу владельца, состояние и следующий шаг (2026-11-10)

Работа остановлена по приказу (оркестратор, 08:16 UTC). Три задачи Sonnet прерваны; их незаконченные файлы
закоммичены как WIP этим же коммитом. Ферм от L5 не запущено (готовых автономных сценариев нет; выгрузка шрифтов
`farm/fontstate_job.sh` у L4 уже отработала).

### Состояние в числах
- Последняя влитая голова: 3a5f2e7 → `claude/fnv-nx-clean` 3574f819 (вердикт L1 зелёный).
- Последняя ЗЕЛЁНАЯ голова ветки: 39cc8b4 — `tests/s4` 300 тестов / 336 768 проверок / 0 провалов; `tests/s7` 63 / 2 321 / 0.
  После неё в ветке только WIP (ниже): на WIP-голове наборы НЕ проверялись и могут не собираться.
- Манифест: 440 уникальных системных импортов. Измеренный повторный аудит (fnv-nx-build `l5-platform/manifest`,
  audit2/): тотально 305, частично 51, заглушка 5, отсутствует 49, чужих 30 (29 msvcrt у L3, d3d9 у L6).
- Зеркала в fnv-nx-build: `tools/nls` (Wine 9.0 .nls + сгенерированная таблица), `tools/fonts` (шрифты Wine, Liberation,
  `prefix/` — выгрузка L4: 50 .fon, реестр 10 веток, fc-list, эталонный `fontdump.txt` 344 строки),
  `tools/freetype` (2.13.2+dfsg-1ubuntu0.2, исходный пакет эталона), `l5-platform/manifest` (imports, closure,
  dynsites, bridge.tsv на 440 импортов, audit, audit2).

### Незаконченное (WIP в этом коммите)
| Задача | Файлы | Где остановлено |
|---|---|---|
| Сводка утечки указателей импортов для L3 (по бэкенду Horizon, libnx b156b47 в /home/user/gd/libnx — контейнерный путь, пропадёт) | `platform/manifest/import_escape.py`, `test_import_escape.sh`, `escape_fixture/` | инструмент писался; таблица `import_escape.tsv` НЕ выдана, L3 её ждёт |
| Шрифтовой gdi32, этап 1 (FreeType эталона, метрики и GetGlyphOutline против `fontdump.txt`) | `platform/win32/gdifont.cpp`, `gdi_int.h`, правки `gdi32.cpp`, `tests/s4/Makefile`, `tests/s4/test_gdifont.cpp`, `tools/fetch_freetype.sh` | в середине реализации; число совпавших строк эталона не снято |
| kernel32: соответствие ENV-TRUTH и 20 отсутствующих функций | файлов нет | исполнитель остановлен до первой записи: в дереве от этой задачи ничего нет, начинать с нуля по прежнему ТЗ |

### Точный следующий шаг при возобновлении
1. `git checkout` ветки, собрать `tests/s4` и `tests/s7` (нужны `NLS_DIR`, `FNVNX_FONT_REF`, `ext/freetype` — см.
   `tests/s4/Makefile` и `platform/win32/tools/fetch_freetype.sh`); снять число провалов на WIP-голове.
2. Довести три задачи выше, каждую отдельным исполнителем с прежним ТЗ (приёмка: набор зелёный; для шрифтов — побайтное
   равенство со всеми строками `glyph`/`metrics` эталона; для утечки — таблица в `l5-platform/manifest`).
3. Затем по аудиту audit2/all.tsv: kernel32 (Virtual*, отображение файлов, профили INI), ole32, user32
   (MessageBoxA, ChangeDisplaySettingsA, GetSystemMetrics), шрифтовой gdi32 этапы 2–4, DSP эффектов DirectSound,
   интеграция с загрузчиком (регистрация образов, куча в окне гостя, главный поток), `merge` через MERGER.

### Известные расхождения с ENV-TRUTH на зелёной голове 39cc8b4 (не исправлены до конца)
`SteamAPI_Init`/`IsSteamRunning` возвращают true (M6: false); консольные хэндлы отвечают успехом (F5);
`DebugBreak` не поднимает исключение (P3); `GetModuleHandle`/`LoadLibrary` знают 6 системных DLL из 14 (M5);
нет `GetVersionExA` и системных каталогов (P4, P5).
