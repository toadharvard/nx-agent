# ENV-TRUTH: состояния среды (утверждено владельцем через оркестратора 2026-11-10)

Правило R10б (MASTER_PLAN4): импорт вправе вернуть результат, который контракт задаёт для состояния среды,
если это состояние (1) истинно на Switch и (2) допустимо для оригинала на ПК. Строка таблицы — не заглушка:
реализация импорта остаётся тотальной по аргументам, таблица фиксирует только состояние мира, которое она видит.

Все строки — решения владельца (**Р**). Таблицу меняет только владелец.

## 1. Модули (что есть в «системе»)

| # | Состояние | Импорты | Результат по контракту | Ст. |
|---|---|---|---|---|
| M1 | Несистемные PE игры присутствуют и переводятся лифтером (R10в): `binkw32.dll`, `libvorbisfile.dll`, `libvorbis.dll`, `atimgpud.dll`, `d3dx9_38.dll`, `d3dx9_31.dll` (решения оркестратора; d3dx9_31 грузится из d3dx9_38 по константному имени, достижимость по графу L1) | статические импорты exe; `LoadLibraryA("ATIMGPUD.dll")` | модуль загружен, его код — lifted; его собственные импорты входят в манифест | Р |
| M2 | Драйверных DLL производителей GPU нет: `nvapi.dll`, `atiadlxx.dll`, `atiadlxy.dll` | `LoadLibraryA` | `NULL`, `ERROR_MOD_NOT_FOUND` (как на ПК без этого драйвера) | Р |
| M3 | `dbghelp.dll` нет (Havok StackTracer) | `LoadLibraryA` | `NULL`, `ERROR_MOD_NOT_FOUND` | Р |
| M4 | `mscoree.dll` не загружен (процесс не .NET) | `GetModuleHandleW` | `NULL`, `ERROR_MOD_NOT_FOUND` | Р |
| M5 | Системные DLL из таблицы импорта (`kernel32`, `user32`, `gdi32`, `advapi32`, `shell32`, `ole32`, `comctl32`, `winmm`, `wsock32`, `dsound`, `dinput8`, `xinput1_3`, `d3d9`) «загружены»; `GetProcAddress` отвечает по таблице экспортов манифеста, для прочих имён — `NULL`, `ERROR_PROC_NOT_FOUND` | `GetModuleHandle*`, `LoadLibraryA`, `GetProcAddress` | экспорт вне манифеста, запрошенный по константному имени (`EncodePointer`, `DecodePointer`, `IsProcessorFeaturePresent`, `InitializeCriticalSectionAndSpinCount`, `Direct3DCreate9`, `Direct3DShaderValidatorCreate9`…), обязан быть в манифесте: такие имена собирает перепись мест вызова | Р |
| M6 | Клиент Steam/Galaxy не запущен. GalaxyWrp.dll и Galaxy.dll не переводятся: 11 импортов exe суть функции Steamworks API с публичным контрактом | 11 импортов `galaxywrp.dll` | `SteamAPI_Init` = `false`, `SteamAPI_IsSteamRunning` = `false`, аксессоры интерфейсов = `NULL`, остальное по документации Steamworks для неинициализированного API. Оригинал под Wine без клиента — только проверка | Р |
| M7 | Модулей `usp10.dll`, `ddraw.dll`, `mss32.dll`, `d3d9d.dll` нет (слово владельца через оркестратора, 2026-11-11: usp10 — d3dx идёт проверенным путём GDI `ExtTextOut`; ddraw — Bink другим путём, вернуться, если на прогоне Bink без него не работает; mss32 — файла нет в поставке GOG; d3d9d — отладочный). Имена, которые игра берёт из них по `GetProcAddress` (вывод L1, `gpa_names.tsv`): 16 функций Uniscribe, `DirectDrawCreate`, 24 функции Miles, `DebugSetMute` | `LoadLibraryA`, `GetModuleHandleA`, `GetProcAddress` | `LoadLibrary` → `NULL`, `ERROR_MOD_NOT_FOUND`; `GetProcAddress` по контракту отсутствующего модуля | Р |

## 2. Сеть

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| N1 | Winsock есть; сетевых интерфейсов, кроме loopback, нет: приложение не запрашивает соединение у системы (nifm), поэтому это истинно при любом состоянии Wi-Fi консоли | 21 импорт `wsock32` | Полное отображение Winsock 1.1 на BSD-сокеты Horizon (коды ошибок, `WSAAsyncSelect` → оконные сообщения). Результаты «нет сети» (`WSAENETUNREACH`, `WSAHOST_NOT_FOUND`) возникают из самой среды, а не из таблицы; loopback работает | Р |

## 3. Ввод и отдача

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| I1 | Один контроллер XInput с индексом 0 (портативный режим или геймпад); индексы 1-3 не подключены | `XInputGetState`, `XInputSetState` | 0: `ERROR_SUCCESS`, раскладка 1:1; 1-3: `ERROR_DEVICE_NOT_CONNECTED`; вибрация → HD rumble | Р |
| I2 | DirectInput: есть системная клавиатура и системная мышь; класс игровых контроллеров пуст (геймпад виден только через XInput) | `DirectInput8Create` → `EnumDevices`, `CreateDevice` | перечисление по контракту для этого набора устройств | Р |
| I3 | Устройств с force feedback в DirectInput нет | `EnumDevices(DIEDFL_FORCEFEEDBACK)`, `CreateEffect`, `GetForceFeedbackState`… | пустое перечисление; `DIERR_UNSUPPORTED` по контракту для устройства без FF | Р |
| I4 | Клавиатура: события порождает swkbd (полный поток: keydown/char/keyup на символ) и USB-клавиатура, если подключена | `GetAsyncKeyState`, сообщения окна, DInput keyboard | по контракту | Р |
| I5 | Мышь: устройство есть. Источник движения: сенсорный экран в портативе (абсолютный указатель); в доке — только USB-мышь, если подключена | DInput mouse, `GetCursorPos` (Bink), `SwapMouseButton`, `GetDoubleClickTime` | по контракту; кнопки мыши не переставлены, двойной щелчок 500 мс | Р |

## 4. Экран и окно

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| D1 | Один адаптер, один монитор; режим рабочего стола 1280×720×32@60 (портатив) или 1920×1080×32@60 (док) | `EnumDisplayDevicesA`, `GetSystemMetrics`, `ChangeDisplaySettingsA`, `GetDeviceCaps` | по контракту для этого набора режимов | Р |
| D2 | Других окон и процессов с окнами нет | `FindWindowA`, `GetTopWindow`, `EnumChildWindows`, `SetForegroundWindow` | `FindWindowA` чужого класса → `NULL`; окно игры всегда активно и на переднем плане | Р |
| D3 | `MessageBoxA`: текст показывается модально средствами платформы, возвращается нажатая кнопка | `MessageBoxA` | полный набор `MB_*` кнопок; нужен собственный модальный диалог поверх кадра (у Horizon нет апплета выбора) | Р |
| D4 | Оболочки нет: ассоциаций файлов и браузера нет | `ShellExecuteA` | `SE_ERR_NOASSOC` (31) | Р |
| D5 | Видеопамять адаптера: 960 МиБ, одинаково в доке и портативе. Оценка: пул приложения 3 285 − окно гостя 2 048 − код 93 − рантайм 64 − прочее 32 − шейдеры 87; пересчитывается после замера | `GetAvailableTextureMem` (L6), реестр Wine `VideoMemorySize` у эталона | объём минус выделенное | Р (оркестратор, профиль ENV D3D9) |

## 5. Файлы, реестр, пользователь

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| F1 | Один диск `C:` (фиксированный), корень — каталог порта на SD; игра установлена по пути GOG-установщика этой версии | `GetDriveTypeA`, `GetFullPathNameA`, `GetCurrentDirectoryA`, `GetModuleFileNameA` | `DRIVE_FIXED`; прочие буквы — `DRIVE_NO_ROOT_DIR` | Р |
| F2 | «Мои документы» = `C:\Users\<user>\Documents`; сохранения в `…\My Games\FalloutNV\` | `SHGetFolderPathA` | все CSIDL по контракту в дереве `C:\Users\<user>\` | Р |
| F3 | Реестр — настоящий постоянный куст (файл на SD). Содержимое по правилу владельца: ровно те ключи, которые читают exe и модули, найденные статически по местам вызовов (`regsites.py`, поток данных); значения берутся из объявленной машины (`declared_registry.tsv`, `Installed Path` = каталог игры); ключ без объявленного значения отсутствует (ERROR_FILE_NOT_FOUND); сценарий установщика не нужен | 5 импортов `Reg*` | по контракту | Р |
| F4 | Имя пользователя — `Player` | `GetUserNameA` | по контракту | Р |
| F5 | Консоли нет (GUI-процесс): стандартных хэндлов нет | `GetStdHandle`, `GetConsoleMode`, `GetConsoleCP`, `WriteConsoleA/W` | `GetStdHandle` = `NULL`; консольные вызовы — отказ с `ERROR_INVALID_HANDLE`; `GetConsoleCP` = 0 | Р |
| F6 | Командная строка: `"<путь установки>\FalloutNV.exe"` без аргументов; окружение — минимальный блок Windows | `GetCommandLineA/W`, `GetEnvironmentStrings*` | по контракту | Р |

## 6. Локаль и время

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| L1 | ACP 1252, OEMCP 437, пользовательская локаль en-US (0x0409), по языку данных игры владельца | `GetACP`, `GetOEMCP`, `GetCPInfo`, `GetLocaleInfoA/W`, `LCMapString*`, `CompareString*`, `GetStringType*`, `IsValidLocale`, `EnumSystemLocalesA`, `IsValidCodePage` | таблицы NLS для en-US/1252/437 и UTF-16 полные; прочие локали и страницы — «не установлены» (`FALSE`) | Р |
| L2 | Часовой пояс и время — из службы времени Switch | `GetLocalTime`, `GetSystemTime*`, `GetTimeZoneInformation`, `FileTimeToLocalFileTime`, `SystemTimeToTzSpecificLocalTime` | по контракту | Р |
| L3 | `QueryPerformanceFrequency` = частота монотонного таймера платформы (константа); `GetTickCount`, `timeGetTime` — от того же таймера | 4 импорта | по контракту | Р |

## 7. Процесс и железо

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| P1 | Процессоров 3 (ядра 0-2), архитектура x86, страница 4 КиБ, гранулярность 64 КиБ, адреса 0x00010000-0x7FFEFFFF | `GetSystemInfo`, `SetThreadIdealProcessor` | честное число; идеальный процессор 0-2 | Р |
| P2 | Физическая память = измеренный бюджет приложения Switch; виртуальная 2 ГиБ | `GlobalMemoryStatus(Ex)`, `VirtualAlloc`, `Heap*` | исчерпание → `NULL`, `ERROR_NOT_ENOUGH_MEMORY` | Р |
| P3 | Отладчика нет | `IsDebuggerPresent`, `OutputDebugStringA`, `DebugBreak` | `FALSE`; строка никому не доставляется (это и есть контракт без отладчика); `DebugBreak` → исключение `EXCEPTION_BREAKPOINT` через R4 | Р |
| P4 | Версия Windows — 6.1.7601 SP1 (Windows 7), платформа NT | `GetVersionExA` (Bink, Vorbis) | по контракту | Р |
| P5 | Системный каталог `C:\Windows\system32`, каталог Windows `C:\Windows` (существуют, пустые) | `GetSystemDirectoryA`, `GetWindowsDirectoryA` | по контракту | Р |
| P6 | Начальное состояние FP нового гостевого потока: FCW = 0x027F (исключения замаскированы, точность 53 бита, округление к ближайшему), MXCSR = 0x1F80, стек x87 пуст (TW = 0xFFFF), FSW = 0. Источник: начальный CONTEXT потока 32-битного процесса; эталон Wine 9.0 `dlls/ntdll/unix/signal_i386.c:2496-2498` (`ControlWord = 0x27f`, `MxCsr = 0x1f80`). Внесено по указанию оркестратора (строка от L3b, 2026-11-11) | вход потока лифтера (`rt/enter.c`), `CreateThread` | по контракту | Р |

## 8. Мультимедиа

| # | Состояние | Импорты | Результат | Ст. |
|---|---|---|---|---|
| A1 | Одно устройство вывода звука (первичное): 48 кГц, стерео, 16 бит; аппаратного микширования и EAX нет | `DirectSoundCreate8` → `GetCaps`, `IKsPropertySet` | программные буферы; запрос свойств EAX → `E_PROP_ID_UNSUPPORTED` | Р |
| A2 | Одно устройство `waveOut` (то же) | 11 импортов `waveOut*` (Bink) | по контракту | Р |
| A3 | Фильтры DirectShow: парсер MPEG-1 audio + декодер MP3, парсер WAVE, рендерер DirectSound. Прочих кодеков нет | `CoCreateInstance(CLSID_FilterGraph)`, `CoCreateInstance(CLSID_FilterMapper)` | `Render` потока иного типа → `VFW_E_CANNOT_RENDER`; `IFilterMapper` перечисляет ровно этот набор | Р |

## Следствия для работ

- F1: путь установки известен (объявленная машина). F3: ключи реестра — по статическим местам чтения (`regsites.py`), без сценария установщика.
- M6: Galaxy.dll и GalaxyWrp.dll исключены из MODULE-LIFT; msvcr110/msvcp110/ws2_32/bcrypt в манифест не входят.
- N1: Winsock 1.1 → BSD-сокеты Horizon, nifm не запрашивается.
