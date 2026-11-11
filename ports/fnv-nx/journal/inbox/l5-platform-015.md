## L5 PLATFORM — заметка 015 (2026-11-11): опись допущений для `verify/ASSUMPTIONS.json`

Голова `claude/fnv-l5-platform`: 935d2355. Только опись, кода не менял. Строки — `ports/fnv-nx/<путь>:<строка>`.
Классы: OWNER-<одно из восьми> | FACT (первоисточник назван) | GATE (какой гейт закроет) | OPEN (источника нет).
Восемь OWNER: компилятор верен; SHA-256; Win32/D3D9 как в документации; поведение консоли; санитайзеры вместо доказательства;
нет диких обращений гостя; ILP32; IEEE FP.

### platform/win32/winsock.cpp (сеть объявленной машины)
- :177 «Assumption: the host kernel answers as Linux does, seen through Wine's mapping.» (OP_MCAST_IF EXTERNAL → 10022)
  — OWNER-поведение консоли (ответ ядра консоли на setsockopt; Wine 9.0 `ntdll/unix/socket.c:sock_errno_to_status` —
  FACT только для хоста Linux, не для Horizon).
- :182 «Same assumption; no Windows documentation text states this case.» (OP_MCAST_MEMBER ANY/EXTERNAL → 10022)
  — OWNER-поведение консоли; для самой таблицы кодов FACT уже есть (Wine 9.0 `NtStatusToWSAError`).
- :729 «Assumption (neither source fixes the address): 127.0.0.1.» (собственное имя без сети)
  — OPEN (ни Windows, ни Wine 9.0 `ws2_32/unixlib.c:unix_gethostbyname` адрес не задают; он берётся от резолвера хоста).
- :732 «Assumption: with no DNS server the host resolver answers HOST_NOT_FOUND.» — OPEN
  (Windows docs `gethostbyname` различает WSAHOST_NOT_FOUND и WSATRY_AGAIN; какой при отсутствии DNS — источника нет).
- заметка 013: «`IP_ADD_MEMBERSHIP` на loopback-интерфейс уходит на хост; результат на консоли не проверен» — OWNER-поведение консоли.

### Реестр, шрифты
- заметка 013: «значения `HKLM\Software\Microsoft\Direct3D` (D3DXDoNotMute, DisablePSGP, DisableD3DXPSGP, DisableMMX)
  на объявленной машине отсутствуют» — FACT: решение владельца об объявленной машине (CLAUDE.md 2026-11-11), id нужен
  в реестре; полнота списка ключей — GATE `regsites` (после статического разрешения всех GetProcAddress).
- заметка 014: «Набор шрифтов объявленной машины — шрифты Wine 9.0 (63 файла, 50 .fon)» — FACT: Wine 9.0
  (Ubuntu 9.0~repack-4build3), `fontdump.txt` sha256 закреплён; нужен id как объявление машины.
- заметка 014: «Деревья 09093ea и fcf99a1 различаются вне `fontdump.txt`; различие не разбиралось» — OPEN.
- platform/win32/gdifont.cpp:14 «Stage 1 assumptions: display DC at 96 dpi, MM_TEXT, identity world transform; system
  locale en-US (0x409); TC_RA_ABLE; no vertical (@) families; no FontLink, no fontconfig-derived family fallbacks»
  — FACT: Wine 9.0 `dlls/win32u/dc.c` (начальное состояние DC), `font.c`/`freetype.c` (семьи, FontLink); dpi и локаль —
  объявленная машина (id).
- platform/win32/gdifont.cpp:1213 «Assumptions stated here once: the DC is GM_COMPATIBLE with the identity world
  transform (no SetWorldTransform / SetGraphicsMode exists in this DC) ... no inter-character extra spacing, no
  justification, no layout mirroring, no clip region» — GATE: список импортов exe/модулей (нет вызовов
  SetWorldTransform/SetGraphicsMode/SetTextCharacterExtra/SetTextJustification/SetLayout/SelectClipRgn); значения по
  умолчанию — FACT: Wine 9.0 `win32u/dc.c`.

### platform/win32/nls.cpp:18 (R1–R5)
- :19 R1 «the installed code pages and locales are those above, not Wine's full set» — FACT: объявленная машина (id);
  состав — решение, не измерение.
- :20 R2 «EnumSystemLocalesA lists the one en-US locale» — следует из R1 (то же объявление).
- :21 R3 «arguments Wine does not check but cannot survive ... fail with ERROR_INVALID_PARAMETER» — FACT-кандидат:
  Windows docs GetStringTypeW/LCMapStringW (ERROR_INVALID_PARAMETER); для случаев вне документации — OPEN.
- :23 R4 «LCMAP_HASH and LCMAP_SORTHANDLE return 0 without a last error, as Wine does» — FACT:
  Wine 9.0 `dlls/kernelbase/locale.c:LCMapStringEx` (ссылку в тексте добавить).
- :24 R5 «user registry overrides of locale values do not exist» — GATE `regsites` (нет мест чтения
  `Control Panel\International`, кроме объявленных) + объявленная машина.

### Прочее в platform/, toolchain/guest/, loader/
- platform/os/os_horizon.c:257 «Assumption (the header's contract): GetThreadContext3 succeeds only for a paused,
  descheduled thread.» — OWNER-поведение консоли (контракт `hostapi.h`; Switchbrew SVC 0x7D/GetThreadContext3 — справка).
- toolchain/guest/os_guest.c:306-309 «FAT32/exFAT on the SD card fold case ... not checked on hardware yet (NX1) ...
  the SD behaviour is assumed» — OWNER-поведение консоли.
- platform/manifest/import_escape.py:86 «ASSUMPTION (target substitution): clang-18 cannot emit the product triple ...
  the guest table is built for arm64_32-apple-watchos» — OPEN (нужен гейт: свойства `nocapture`/`readonly` не зависят
  от триплета; доказательства нет, класс ILP32 здесь не подходит — это подмена цели анализа).
- platform/manifest/import_escape.py:97 «volatile accesses are plain memory accesses for capture purposes; an integer
  parameter is not a pointer; pointers are not smuggled into integers (inttoptr/ptrtoint chains are not followed)»
  — GATE: проход по IR, отвергающий `ptrtoint`/`inttoptr` на путях параметров (гейт L1 `import_escape`); volatile —
  OWNER-компилятор верен.
- platform/manifest/import_escape.py:296 «Assumption stated in the output header: volatile is not used as a side
  channel that retains the address.» — GATE (тот же проход) / OWNER-компилятор верен.
- platform/manifest/pemanifest.py:20 «Assumptions: the function starts at the nearest preceding `push ebp; mov ebp,esp`
  ... or at the 16-aligned address after int3 padding, and ends at the next prologue; control flow is followed forward
  only» — GATE: таблица функций лифтера (L3), построенная правилом из exe, и сверка с ней манифеста.
- platform/manifest/regsites.py:22 «Assumptions: as for pemanifest.py (function boundaries, forward-only control flow,
  no ebp slot written through a pointer)» — GATE: тот же.
- loader/synth/synth_image.py:28 и :982 «assumed, guessed and unknown bytes ... Guessed: bytes no object states (soft
  zero padding, items made from a name alone)» — OPEN (категория «догадка по имени» запрещена правилом 2026-11-10;
  гейт «guessed = 0» требует правила для каждой категории; решает L2).

### Журнал L5 (старые заметки, открытые «по памяти»)
- journal/inbox/l5-platform-010.md:48 «GetSystemMetrics: часть метрик записана по памяти о Wine, не сверена с исходником»
  — FACT: Wine 9.0 `dlls/win32u/sysparams.c:get_system_metrics` (архив Wine 9.0 в контейнере).
- l5-platform-010.md:54 «Источники 6 строк таблицы констант (пины DirectShow) записаны по памяти — сверить с MSDN»
  — FACT: документация DirectShow (MSDN: пины `IPin`/`AM_MEDIA_TYPE` конкретных фильтров); без сверки — красный.
- l5-platform-010.md:51 «Значения порта для Fallout.ini (bUseThreaded*) больше нигде не задаются» — OPEN.
- l5-platform-009.md:34 «На хосте GetSystemInfo даёт число ядер машины, не 3 (P1); бэкенд Horizon даёт 3, гостевой
  бэкенд не проверен» — GATE: тест backend=declared machine (P1 = 3 ядра, ENV_TRUTH P1).
- l5-platform-009.md:35 «`DATE_LTRREADING`/`RTLREADING`/`AUTOLAYOUT` принимаются, меток направления не добавляют: правило
  для en-US не найдено» — OPEN.
- l5-platform-006.md:23 «точность `%.800e` libc консоли как источник точного разложения в printf — допущение»
  — OWNER-поведение консоли (libc консоли — внешняя библиотека по документации).

### Вне ветки L5, но в области bootmachine/bootsock (`origin/claude/fnv-nx-clean`, владелец L4)
- verify/bootmachine.py:56 и :150 «the two kernel codes are the header owner's statement from memory; assumption of the
  stage» (0xF201 «ResultBusy», 0xFA01 «ResultInvalidState») — FACT: см. ниже (задача B).
- verify/bootmachine.py:34 и :646 «every instant of the years 1970..2100 ... stated assumption of the stage» — OPEN
  (границы диапазона часового пояса источника не имеют).
- verify/bootsock.py:11 A1 «handle numbers: lowest number >= 3 not open» — OPEN (заголовок номер не задаёт; модель
  выбирает; «гость не должен зависеть» — гейт на зависимость от номера отсутствует).
- verify/bootsock.py A2 «the host's loopback has 127.0.0.0/8 and ::1; every other unicast address is not local» —
  OWNER-поведение консоли.
- verify/bootsock.py A3 «getaddrinfo with no network: numeric node parsed ..., list = one record per socket type
  STREAM/DGRAM/RAW in this order» — OWNER-поведение консоли (bsd getaddrinfo Horizon); POSIX — справка.
- verify/bootsock.py A4 «EPROTONOSUPPORT: STREAM takes protocol 0 or 6, DGRAM 0 or 17 ...» — OWNER-поведение консоли.

### Задача B — коды ядра 0xF201 / 0xFA01 (результат)
- Файл `verify/bootmachine.py` есть только на `origin/claude/fnv-nx-clean` (владелец L4), в ветке L5 его нет; правки не
  вносил. Источник проверен по заголовкам libnx, закреплённым `tools/fetch.sh:19` (`switchbrew/libnx` b156b4774fb2df7b93402169509266da7547fc4c).
- `include/switch/result.h`: `MAKERESULT(module,description) = (module & 0x1FF) | (description & 0x1FFF) << 9`;
  `Module_Kernel = 1`.
- 0xFA01 = 1 | 125<<9 = `KernelError_InvalidState` (125). Подтверждено, ошибки нет.
- 0xF201 = 1 | 121<<9 = `KernelError_NotFound` (121), а не «ResultBusy»: в `KernelError_*` перечисления нет Busy
  (`LibnxNvidiaError_Busy` — другой модуль). Имя в `bootmachine.py:150` («ResultBusy») неверно.
- Остальные константы того же файла (:158-159): 0xCA01 = InvalidSize(101), 0xCC01 = InvalidAddress(102),
  0xD801 = InvalidMemoryPermissions(108), 0xE401 = InvalidHandle(114); 0xD401 = `KernelError_InvalidMemoryState`(106),
  в файле назван «InvalidCurrentMemory» — в libnx такого имени нет.
