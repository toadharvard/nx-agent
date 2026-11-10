# ENV-TRUTH: состояния среды (черновик 1, L5, 2026-11-10)

Правило R10б (MASTER_PLAN4): импорт вправе вернуть результат, который контракт задаёт для состояния среды,
если это состояние (1) истинно на Switch и (2) допустимо для оригинала на ПК. Строка таблицы — не заглушка:
реализация импорта остаётся тотальной по аргументам, таблица фиксирует только состояние мира, которое она видит.

Статус строк: **Р** — следует из уже принятых решений владельца; **?** — нужно решение владельца.

## 1. Модули (что есть в «системе»)

| # | Состояние | Импорты | Результат по контракту | Ст. |
|---|---|---|---|---|
| M1 | Несистемные PE игры присутствуют и переводятся лифтером (R10в): `binkw32.dll`, `libvorbisfile.dll`, `libvorbis.dll`, `atimgpud.dll` | статические импорты exe; `LoadLibraryA("ATIMGPUD.dll")` | модуль загружен, его код — lifted; его собственные импорты входят в манифест | Р |
| M2 | Драйверных DLL производителей GPU нет: `nvapi.dll`, `atiadlxx.dll`, `atiadlxy.dll` | `LoadLibraryA` | `NULL`, `ERROR_MOD_NOT_FOUND` (как на ПК без этого драйвера) | Р |
| M3 | `dbghelp.dll` нет (Havok StackTracer) | `LoadLibraryA` | `NULL`, `ERROR_MOD_NOT_FOUND` | ? |
| M4 | `mscoree.dll` не загружен (процесс не .NET) | `GetModuleHandleW` | `NULL`, `ERROR_MOD_NOT_FOUND` | Р |
| M5 | Системные DLL из таблицы импорта (`kernel32`, `user32`, `gdi32`, `advapi32`, `shell32`, `ole32`, `comctl32`, `winmm`, `wsock32`, `dsound`, `dinput8`, `xinput1_3`, `d3d9`, `d3dx9_38`) «загружены»; `GetProcAddress` отвечает по таблице экспортов манифеста, для прочих имён — `NULL`, `ERROR_PROC_NOT_FOUND` | `GetModuleHandle*`, `LoadLibraryA`, `GetProcAddress` | экспорт вне манифеста, запрошенный по константному имени (`EncodePointer`, `DecodePointer`, `IsProcessorFeaturePresent`, `InitializeCriticalSectionAndSpinCount`, `Direct3DCreate9`…), обязан быть в манифесте: такие имена собирает перепись мест вызова | Р |
| M6 | **GalaxyWrp.dll / Galaxy.dll.** Варианты: (а) R10в буквально — перевести оба PE (4,86 МБ x86) и реализовать их системные импорты: +391 уникальный импорт (`msvcp110` 123, `msvcr110` 127, `ws2_32` 38, `bcrypt` 21, `kernel32` +58…); (б) R10б — 11 импортов exe из GalaxyWrp суть функции Steamworks API (`SteamAPI_Init`, `SteamUser`, …) с публичным контрактом; состояние «клиент Steam/Galaxy не запущен» → `SteamAPI_Init` = `false`, `SteamAPI_IsSteamRunning` = `false`, аксессоры интерфейсов = `NULL`, остальное по документации для неинициализированного API | 11 импортов `galaxywrp.dll` | рекомендую (б); проверка: оригинал под Wine без клиента Galaxy, результаты этих 11 вызовов | ? |

## 2. Сеть

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| N1 | Winsock есть; сетевых интерфейсов, кроме loopback, нет: приложение не запрашивает соединение у системы (nifm), поэтому это истинно при любом состоянии Wi-Fi консоли | 21 импорт `wsock32` | Полное отображение Winsock 1.1 на BSD-сокеты Horizon (коды ошибок, `WSAAsyncSelect` → оконные сообщения). Результаты «нет сети» (`WSAENETUNREACH`, `WSAHOST_NOT_FOUND`) возникают из самой среды, а не из таблицы; loopback работает | ? |

## 3. Ввод и отдача

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| I1 | Один контроллер XInput с индексом 0 (портативный режим или геймпад); индексы 1-3 не подключены | `XInputGetState`, `XInputSetState` | 0: `ERROR_SUCCESS`, раскладка 1:1; 1-3: `ERROR_DEVICE_NOT_CONNECTED`; вибрация → HD rumble | Р |
| I2 | DirectInput: есть системная клавиатура и системная мышь; класс игровых контроллеров пуст (геймпад виден только через XInput) | `DirectInput8Create` → `EnumDevices`, `CreateDevice` | перечисление по контракту для этого набора устройств | ? |
| I3 | Устройств с force feedback в DirectInput нет | `EnumDevices(DIEDFL_FORCEFEEDBACK)`, `CreateEffect`, `GetForceFeedbackState`… | пустое перечисление; `DIERR_UNSUPPORTED` по контракту для устройства без FF | Р |
| I4 | Клавиатура: события порождает swkbd (полный поток: keydown/char/keyup на символ) и USB-клавиатура, если подключена | `GetAsyncKeyState`, сообщения окна, DInput keyboard | по контракту | Р |
| I5 | Мышь: устройство есть. Источник движения — ? (сенсорный экран в портативе как абсолютный указатель; в доке движения нет) | DInput mouse, `GetCursorPos` (Bink), `SwapMouseButton`, `GetDoubleClickTime` | по контракту; кнопки мыши не переставлены, двойной щелчок 500 мс | ? |

## 4. Экран и окно

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| D1 | Один адаптер, один монитор; режим рабочего стола 1280×720×32@60 (портатив) или 1920×1080×32@60 (док) | `EnumDisplayDevicesA`, `GetSystemMetrics`, `ChangeDisplaySettingsA`, `GetDeviceCaps` | по контракту для этого набора режимов | Р |
| D2 | Других окон и процессов с окнами нет | `FindWindowA`, `GetTopWindow`, `EnumChildWindows`, `SetForegroundWindow` | `FindWindowA` чужого класса → `NULL`; окно игры всегда активно и на переднем плане | Р |
| D3 | `MessageBoxA`: текст показывается модально средствами платформы, возвращается нажатая кнопка | `MessageBoxA` | полный набор `MB_*` кнопок; нужен собственный модальный диалог поверх кадра (у Horizon нет апплета выбора) | Р |
| D4 | Оболочки нет: ассоциаций файлов и браузера нет | `ShellExecuteA` | `SE_ERR_NOASSOC` (31) | ? |

## 5. Файлы, реестр, пользователь

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| F1 | Один диск `C:` (фиксированный), корень — каталог порта на SD; игра установлена в `C:\FalloutNV\` | `GetDriveTypeA`, `GetFullPathNameA`, `GetCurrentDirectoryA`, `GetModuleFileNameA` | `DRIVE_FIXED`; прочие буквы — `DRIVE_NO_ROOT_DIR` | ? |
| F2 | «Мои документы» = `C:\Users\<user>\Documents`; сохранения в `…\My Games\FalloutNV\` | `SHGetFolderPathA` | все CSIDL по контракту в дереве `C:\Users\<user>\` | Р |
| F3 | Реестр — настоящий постоянный куст (файл на SD). Начальное содержимое: ключ установщика `HKLM\SOFTWARE\Bethesda Softworks\FalloutNV` с `Installed Path` = `C:\FalloutNV\` | 5 импортов `Reg*` | по контракту | ? |
| F4 | Имя пользователя — ник выбранной учётной записи Switch, приведённый к CP1252; иначе фиксированное `Player` | `GetUserNameA` | по контракту | ? |
| F5 | Консоли нет (GUI-процесс): стандартных хэндлов нет | `GetStdHandle`, `GetConsoleMode`, `GetConsoleCP`, `WriteConsoleA/W` | `GetStdHandle` = `NULL`; консольные вызовы — отказ с `ERROR_INVALID_HANDLE`; `GetConsoleCP` = 0 | Р |
| F6 | Командная строка: `"C:\FalloutNV\FalloutNV.exe"` без аргументов; окружение — минимальный блок Windows | `GetCommandLineA/W`, `GetEnvironmentStrings*` | по контракту | Р |

## 6. Локаль и время

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| L1 | ACP 1252, OEMCP 437, пользовательская локаль en-US (0x0409) | `GetACP`, `GetOEMCP`, `GetCPInfo`, `GetLocaleInfoA/W`, `LCMapString*`, `CompareString*`, `GetStringType*`, `IsValidLocale`, `EnumSystemLocalesA`, `IsValidCodePage` | таблицы NLS для en-US/1252/437 и UTF-16 полные; прочие локали и страницы — «не установлены» (`FALSE`) | ? |
| L2 | Часовой пояс и время — из службы времени Switch | `GetLocalTime`, `GetSystemTime*`, `GetTimeZoneInformation`, `FileTimeToLocalFileTime`, `SystemTimeToTzSpecificLocalTime` | по контракту | Р |
| L3 | `QueryPerformanceFrequency` = частота монотонного таймера платформы (константа); `GetTickCount`, `timeGetTime` — от того же таймера | 4 импорта | по контракту | Р |

## 7. Процесс и железо

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| P1 | Процессоров 3 (ядра 0-2), архитектура x86, страница 4 КиБ, гранулярность 64 КиБ, адреса 0x00010000-0x7FFEFFFF | `GetSystemInfo`, `SetThreadIdealProcessor` | честное число; идеальный процессор 0-2 | Р |
| P2 | Физическая память = измеренный бюджет приложения Switch; виртуальная 2 ГиБ | `GlobalMemoryStatus(Ex)`, `VirtualAlloc`, `Heap*` | исчерпание → `NULL`, `ERROR_NOT_ENOUGH_MEMORY` | Р |
| P3 | Отладчика нет | `IsDebuggerPresent`, `OutputDebugStringA`, `DebugBreak` | `FALSE`; строка никому не доставляется (это и есть контракт без отладчика); `DebugBreak` → исключение `EXCEPTION_BREAKPOINT` через R4 | Р |
| P4 | Версия Windows — 6.1.7601 SP1 (Windows 7), платформа NT | `GetVersionExA` (Bink, Vorbis) | по контракту | ? |
| P5 | Системный каталог `C:\Windows\system32`, каталог Windows `C:\Windows` (существуют, пустые) | `GetSystemDirectoryA`, `GetWindowsDirectoryA` | по контракту | Р |

## 8. Мультимедиа

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| A1 | Одно устройство вывода звука (первичное): 48 кГц, стерео, 16 бит; аппаратного микширования и EAX нет | `DirectSoundCreate8` → `GetCaps`, `IKsPropertySet` | программные буферы; запрос свойств EAX → `E_PROP_ID_UNSUPPORTED` | Р |
| A2 | Одно устройство `waveOut` (то же) | 11 импортов `waveOut*` (Bink) | по контракту | Р |
| A3 | Фильтры DirectShow: парсер MPEG-1 audio + декодер MP3, парсер WAVE, рендерер DirectSound. Прочих кодеков нет | `CoCreateInstance(CLSID_FilterGraph)`, `CoCreateInstance(CLSID_FilterMapper)` | `Render` потока иного типа → `VFW_E_CANNOT_RENDER`; `IFilterMapper` перечисляет ровно этот набор | ? |

## Вопросы владельцу (сводка «?»)

1. M6: Galaxy — вариант (б) (контракт Steamworks, «клиент не запущен») вместо перевода 4,86 МБ и +391 импорта?
2. N1: сеть — честный стек поверх BSD-сокетов Horizon без запроса соединения (loopback есть, внешней сети нет)?
3. I2: геймпад виден только через XInput, в DirectInput класс контроллеров пуст?
4. I5: источник движения мыши — сенсорный экран в портативе; в доке мыши нет (кроме USB)?
5. D4, M3: `ShellExecuteA` → «нет ассоциации»; `dbghelp.dll` отсутствует?
6. F1, F3, F4: раскладка диска `C:`, содержимое реестра установщика, имя пользователя.
7. L1: локаль en-US/1252 (а не язык консоли)?
8. P4: версия Windows 7 SP1?
9. A3: набор фильтров DirectShow — только MP3 и WAVE?
