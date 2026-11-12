# l5-platform-w9-008: незарегистрированные допущения платформы (gate assumptions)

База b525bbe7, ветка claude/fnv-l5-platform-w9-asm. Всего упоминаний слов assum/допущени в platform/ без ASM-id: 8 в .cpp (список лида)
+ 15 в комментариях/докстрингах platform/manifest/*.py и regsites.py (гейт сканирует прозу .py тоже). После правки: 0 без id
(grep -rniE 'assum|допущени' platform | grep -v ASM- = только строки в блоке с id).

## .cpp (8 из списка лида): все сняты (ii), новых допущений нет
- gdifont.cpp:14 — "Stage 1 assumptions" -> значения объявленной машины (96 dpi, MM_TEXT, en-US, TC_RA_ABLE): CLAUDE.md "The declared machine".
- gdifont.cpp:1203 — предусловия, заданные состоянием самого DC (в нём нет сеттеров) = умолчания Win32, ASM-003.
- time.cpp:190 — "ASSUMPTIONS 3" -> ASM-003 (цитата id, OWNER).
- winsock.cpp:177, :182 — net_verdict возвращает значение сам, хоста не спрашивает: ответ хоста не используется; значение = Wine 9.0 (цитируемые строки).
- winsock.cpp:734 — 127.0.0.1: значение hosts-файла объявленной машины (по умолчанию Windows привязывает localhost к 127.0.0.1).
- winsock.cpp:737 — нет DNS и нет записи: значение объявленной машины WSAHOST_NOT_FOUND (win-doc:gethostbyname + wine unixlib.c:host_errno_from_unix).
- nls.cpp:18 R1..R5 — R1,R2,R5 значения объявленной машины; R3 документированная ошибка Windows (ASM-003); R4 Wine 9.0 (решение владельца 2026-11-11).

## platform/manifest/*.py: 11 файлов
- "OWNER n" заменены на id: ASM-001 (компилятор), ASM-003 (Win32 как задокументировано), ASM-006 (нет wild access); ASM-010 (callee-saved), ASM-012, ASM-013 (REMOVE, уже в реестре L1).
- Один новый REMOVE-пункт нужен в реестре (блок L5, 5xx), пишу его id ASM-501 в pemanifest.py, prefs_settings.py, regsites.py, regclass.py, filesites.py, fileset_classes.py, fontfaces.py:
  {"id":"ASM-501","text":"Function boundaries and flow of the manifest passes: a function starts at the nearest preceding `push ebp; mov ebp,esp` (after CC padding, hot-patch mov edi,edi allowed) or, with no prologue in range, at the 16-aligned address after int3 padding, ends at the next prologue; control flow is followed forward only (state joined at forward-jump targets).","used_by":["ports/fnv-nx/platform/manifest/pemanifest.py","ports/fnv-nx/platform/manifest/prefs_settings.py","ports/fnv-nx/platform/manifest/regsites.py","ports/fnv-nx/platform/manifest/regclass.py","ports/fnv-nx/platform/manifest/filesites.py","ports/fnv-nx/platform/manifest/fileset_classes.py","ports/fnv-nx/platform/manifest/fontfaces.py"],"aliases":[],"status":"REMOVE","removed_by":"planned: the function starts and ranges of the exe's L3 fact table (L3a function discovery, N1 classified .text) replace the prologue scan","owner":"L5+L3a"}
  Реестра в моей базе нет (он на claude/fnv-l1-contract-w20-descs): запись вносится вместе с мержем; "used_by" должны содержать id (проверено grep).

## Счёт
owner-cited: 3 .cpp/.py ссылок на OWNER-id в .cpp (time.cpp ASM-003, gdifont 1203 ASM-003, nls R3 ASM-003) + 11 .py (ASM-001/003/006); removed by fact/declared machine: 8 .cpp тексты -> 0 допущений; open: 1 (ASM-501, REMOVE planned, L5+L3a). Новых OWNER нет.
