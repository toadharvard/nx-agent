## J-XXXX: L5 PLATFORM — три первых опыта: манифест, ENV-TRUTH, аудит тотальности (2026-11-10)

Ветка `claude/fnv-l5-platform`; exe-производные таблицы — fnv-nx-build `l5-platform/manifest`.

### 1. Манифест (R10а), `platform/manifest/pemanifest.py`

Правило: манифест = таблицы импорта (статические и delay) всех модулей замыкания загрузки. Загрузчик Windows
разрешает каждый слот до точки входа, поэтому слот входит в контракт загрузки независимо от вызовов. Счётчики ссылок
из `.reloc` — справка.

| Модуль | Слотов | Без ссылок |
|---|---:|---:|
| FalloutNV.exe | 280 (17 DLL) | 0 |
| binkw32.dll | 119 | 0 |
| libvorbisfile.dll | 96 | 0 |
| libvorbis.dll | 73 | 0 |
| GalaxyWrp.dll | 146 | 0 |
| Galaxy.dll | 446 | 0 |

- exe по DLL: kernel32 138, user32 37, d3dx9_38 25, wsock32 21, galaxywrp 11, binkw32 9, winmm 8, ole32 7,
  advapi32 6, libvorbisfile 6, comctl32 3, shell32 3, xinput1_3 2, d3d9 1, dinput8 1, dsound 1, gdi32 1.
- Уникальных системных импортов: exe 254; exe + Bink + Vorbis 299; с Galaxy 690 (+391: msvcr110 127, msvcp110 123,
  kernel32 +58, ws2_32 38, bcrypt 21, …).
- Динамическая привязка в exe: LoadLibraryA 7 мест (nvapi, atiadlxx, atiadlxy, ATIMGPUD, D3D9, USER32, dbghelp),
  GetProcAddress 13, GetModuleHandleW 5 (mscoree — CRT), CoCreateInstance 3 (CLSID_FilterGraph ×1,
  CLSID_FilterMapper ×2). Вне статического замыкания: atimgpud.dll (грузится по имени → MODULE-LIFT),
  ssce5432.dll и GDFFalloutNV.dll (имён в коде замыкания нет).
- COM-поверхность (вне IAT): IDirectSound8 и буферы, IDirectInput8A, граф DirectShow, D3D9 (L6).

### 2. ENV-TRUTH, черновик 1: `platform/manifest/ENV_TRUTH.md`

31 строка состояний, из них 14 требуют решения владельца (9 вопросов). Главный: Galaxy — контракт Steamworks
«клиент не запущен» (11 функций) вместо перевода 4,86 МБ и +391 импорта.

### 3. Аудит существующего `platform/` на тотальность (3 аудитора Sonnet, по телам функций)

| Группа | Всего | TOTAL | PARTIAL | STUB | MISSING |
|---|---:|---:|---:|---:|---:|
| kernel32 | 145 | 60 | 77 | 3 | 5 |
| user32 | 52 | 1 | 26 | 15 | 10 |
| gdi32 | 13 | 0 | 0 | 2 | 11 |
| advapi32 | 6 | 1 | 5 | 0 | 0 |
| comctl32 | 3 | 0 | 0 | 3 | 0 |
| shell32 | 3 | 1 | 1 | 1 | 0 |
| ole32 | 7 | 2 | 1 | 4 | 0 |
| wsock32 | 21 | 2 | 0 | 19 | 0 |
| d3d9 (D3DPERF_SetOptions) | 1 | 0 | 0 | 0 | 1 |
| **импорты, итог** | **251** | **67** | **110** | **47** | **27** |
| winmm (19 импортов) | 20 строк | 5 | 5 | 0 | 10 |
| dsound, методы COM | 118 | 27 | 69 | 2 | 20 |
| DirectShow, методы COM | 94 | 13 | 19 | 37 | 25 |
| dinput8, методы COM | 49 | 8 | 19 | 19 | 3 |
| xinput | 8 | 2 | 2 | 0 | 4 |

d3dx9_38 (25) не аудирован: реализация в render/nine (L6). Таблицы: fnv-nx-build `l5-platform/manifest`, `audit/*.tsv`.

Гейт L5 сейчас: **нереализованных или неполных импортов 184 из 251 (+25 d3dx не проверены, +14 из 19 winmm).**

### 4. Что переносится в чистую раскладку

| Файлы | Решение | Основание |
|---|---|---|
| `crt/*` (3 990 строк + crt_symbols.tsv) | не переносится | CRT статически слинкован в exe: это код exe, тело даёт BODYSEL (R1), а не платформа |
| `audio/vorbisfile.cpp`, `win32/bink.cpp` | не переносится | несистемные PE → MODULE-LIFT (R10в) |
| `win32/iat_calls.cpp`, `crt/msvc_names.c`, `msvc_std_names.cpp` | не переносится | привязка по именам декомпиляции; заменяется гостевым входом импорта (R5) |
| Steam и Winsock в `win32/services.cpp`, null-откат в `audio/backend.cpp` | не переносится | заглушки и откат по сбою (запрещено) |
| `win32/{file,path,handles,memory,thread,sync,waitgraph,time,module,ini,profile,mmio,window}.cpp`, `crt_kernel32.cpp`, `os/*` | переносится как основа, каждая функция доводится до TOTAL | реальные реализации; список пробелов по функции в audit/*.tsv |
| `audio/{dsound,ds3d,mixer,reverb,dshow}.cpp`, `backend_audren.c` | переносится как основа | то же |
| `input/*` | переносится как основа | то же |
| новое: gdi32 (объекты, DIB, ROP3), waveOut, Winsock→BSD, реестр-куст, хуки, NLS-таблицы, модальный MessageBox | писать | MISSING/STUB |

### 5. Бюджет кода (оценка, не замер)

- x86 `.text`: exe 12,44 МБ; Bink + Vorbis + atimgpud 0,56 МБ; Galaxy 3,81 МБ.
- BODYSEL L1 сейчас: native 0, lifted 87 509 (66 698 ждут вердикта N3, 9,75 МБ x86).
- Всё lifted при 6,0× (PoC): ≈ 78 МБ кода A64; с Galaxy ≈ 101 МБ. Если все ждущие N3 станут нативными: ≈ 29 МБ.
- Плюс образ гостя: `.text` только для чтения 12,4 МБ, `.rdata` + `.data` 2,7 МБ; таблицы диспетчера не посчитаны.
- Сравнение с лимитом Switch и кучей игры — после замера кучи (следующая заметка).

### Дальше

1. Жизненный цикл приложения → оконные сообщения по обработчикам игры (задача оркестратора).
2. Импорты CRT-старта exe до TOTAL первыми (HeapCreate, Tls*, GetStartupInfoA, GetCommandLine*, GetEnvironmentStrings*).
3. Нарезка 184 пробелов на задачи Sonnet по файлам-владельцам; конформанс-тесты Wine как падающий тест.
4. Размеры игровых данных против 4 ГиБ FAT32 (данные лежат частями; точные размеры — после сборки файлов).
