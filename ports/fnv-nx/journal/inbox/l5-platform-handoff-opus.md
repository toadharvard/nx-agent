## L5 PLATFORM — передача преемнику (2026-11-11, 22:35Z)

Голова `claude/fnv-l5-platform`: eb6c2c96. Заметки 001–015 в этом каталоге; 016 не написана — её заменяет этот файл.

### Глобальные числа
- `tests/s4`: 498 тестов / 406 189 проверок / 0 провалов; `tests/s7`: 84 / 3 239 / 0 (измерено на ветке работника
  поверх ec2f31a2 + два коммита тулчейна, то есть на содержимом eb6c2c96).
- Гостевая сборка `build_guest.sh` на fnvnx-a64 с msvcmodel: 115 единиц, UNRESOLVED 0 (newlib/libgcc/libstdc++ 141,
  рантайм 18, хуки 14).
- Двойники в тестах (eb7bcf0b; объекты сборки чуть старше): 582 из 582 тестов идут в бинарях с двойниками.
  - 6 файлов определяют 87 символов продукта: `platform/os/os_posix.c` 67 (вместо `os_guest.c`),
    `platform/win32/filemap_shared.cpp` 9, `tests/s4/host_boundary.cpp` 9, `tests/s4/host_gpu.cpp` 1,
    `tests/s4/m32/host_input.cpp` 1, `tests/s7/host_input.cpp` 1.
  - 7 единиц продукта в тестах не слинкованы: `os_guest.c`, `boundary_guest.cpp`, `host_hooks.cpp`, `sock_guest.c`,
    `filemap_private.cpp`, `platform_glue.cpp`, `combridge_exports.gen.cpp` (последняя — только s4).
  - 7 файлов-двойников границы: `tests/s4/host_section.cpp`, `tests/s4/backends/kdouble.h`,
    `tests/s4/sockwire/guest_shim.c`, `host_glue.c`, `tests/s15/mtqemu/linhost.c`, `mlibc.c`, `hb.c`.
  - Таблицы совпадений лежали в scratchpad этой сессии (контейнер не сохранится); пересчёт — `nm --defined-only`
    объектов тестов против объектов продукта.
- Реестр: 20 мест `Reg*`, 16 статических, 4 с динамическим ключом; объявлена 1 строка (`Installed Path`).
- Winsock (2951f7ae, ec2f31a2): до хоста доходят SOCKET только (af 2, type 1, proto 0|6) и (2, 2, 0|17); BIND 127/8 и
  0.0.0.0; CONNECT только 127/8; службы 27, 28, 29, 32, 33 не вызываются.

### Очередь и состояние
1. **Шаг 1: `platform/build.json` как корень fnvnx-recipe/1 — НЕ начат.** Это главное.
   - Компилятор: external `tools/gcc-ilp32-msvcmodel` @ b4057704c0babd012e466cc89181132e02542289, tree_sha256
     b60eafe7a5b0ae3dc679f9bf21e20d3363e3bac2af7379e34127ea5cf8455116 (cc `bin/aarch64-linux-gnu-gcc`, cxx
     `bin/aarch64-linux-gnu-g++`), все плагины стадиями.
   - Библиотеки гостя: external `tools/fnvnx-a64-msvcmodel` @ 00e3d42acd10593d534b9a70bb8940cee7991436, tree_sha256
     3f25152cbc5a729d9e0a12fc5c62b6b912b6ab1571b6b3494de8f35e3904207d (`_Znwj` в ilp32). L1 передал пин своему w4.
     Побитовая воспроизводимость проверяется на ферме L4 (задание tc-repro, session_01KmtZKSzcic61GPMwx3wqug); строка
     TC-REPRO придёт в эту сессию — спросить у L4.
   - L1 пришлёт голову транка и образец `build.json`, когда его w4 (рецепт с плагинами) пройдёт MERGER. Транк был
     a3e9f0a9: 22 гейта, продуктовых корней 0.
   - Сделать при этом: удалить `build_guest.sh`; `platform/build.json` ещё ссылается на удалённый
     `platform/manifest/gate_imports.sh`; `msvcrt_table.gen.cpp` не в списке единиц; external «manifest» должен нести
     `sys_names.tsv`, `specs/` (второй external `tools/wine-specs`) и `regsites.tsv`; строки COM/d3d9 ещё не в
     `extra`/`extra_other` файла `platform/imports.in.json`.
2. **Гейты `imports` и `machine`** (сторона транка): `claude/fnv-l5-platform-w4` @ f15935a9, принято L1, в очереди
   MERGER после его w8/w4/w5. Сторона платформы слита (2951f7ae).
3. **4 места `Reg*` с динамическим ключом** (обёртки 0xc5d670/0xc5d780/0xc5d920): работник запущен, не закончил.
   Гейт `machine` красный («MEASUREMENT build»), пока они не разрешены. L1 ждёт правило и числа «мест вызова
   обёрток всего / разрешено».
4. **Остановка мира под ASan** (`ws2_create_exit_during_stops`): f9a1d268 уже в голове; работник продолжает
   (незакоммичено: `os_world.c/h`, `os_posix.c`, `test_worldstop.cpp`, `check_worldstop.py`).
5. **Двойники → 0** (после шага 1, порядок утверждён оркестратором): тесты s4/s7 становятся гостевыми ELF на
   объектах корня и идут в загрузочной машине L4. L4 (транк c976578): `boot.py run` есть; короткую форму
   `boot.py test --elf --root --out` и `--inject FILE` сделает, когда первый тестовый ELF собран рецептом; обмена
   данными через loopback в модели нет — L4 ждёт точный набор операций и список сервисов/кодов для инъекций.
6. **Объявленная машина, остаток**: FILETIME/SYSTEMTIME, область 1601..30827, граничные ошибки по Wine 9.0 с
   цитатами, поверх TZ_AT; `Fallout.ini` — найти в exe путь копирования `Fallout_default.ini` → ini, чтобы файл
   просто отсутствовал и exe создавал его сам.
7. **Реестр оснований** (`verify/ASSUMPTIONS.json`, L1 пришлёт голову): переделать заметку 015 по ответу L1 —
   FACT без этого слова, с источником; коды ядра (`winsock.cpp:177,182`) → REMOVE по заголовкам libnx; псевдонимы
   ASM-004 остаются только у GetThreadContext3 (`os_horizon.c:257`), регистра имён SD
   (`toolchain/guest/os_guest.c:306`) и IGMP на loopback; GATE 7 и OPEN 10 — запись REMOVE каждой. В заметках 013 и
   014 слово стоит без id — убрать. Решения по OPEN: собственное имя → 127.0.0.1 по Wine 9.0
   `ws2_32/protocol.c`; код 11001 по документации gethostbyname; сравнить деревья `tools/fonts` 09093ea/fcf99a1;
   NLS и DATE_*READING по Wine 9.0 `kernelbase/locale.c`.
8. **Очередь аудита переноса**: DirectShow-заглушки убрать (ветка `-w5`); `crt_symbols.tsv` how=inferred; удалить
   копию загрузчика L5 вместе с `synth_image.py`; vorbis настоящий (libogg 1.3.5 + libvorbis 1.3.7 исходным
   external).

### Работники
- Облачные, не стартовали (лимит Sonnet, сброс 22:40Z), статус failed, работы нет:
  w1 session_01KmtZKSjF1rc6JAiuz1rvJg (DirectShow), w2 session_01KmtZKU6xzxGuGdkrtzwNGD (crt + копия загрузчика),
  w3 session_01KmtZKSqJdpbUBBzAcvizej (vorbis). Ветка `claude/fnv-l5-platform-w1` существует, пустая по смыслу.
- Локальные (живут только в контейнере этой сессии): `l5-regdyn` (п. 3), `l5-stop-asan` (п. 4). Если закончат до
  остановки сессии, их ветки будут отправлены как `claude/fnv-l5-platform-w7` (regdyn) и `-w8` (asan), и преемник
  получит коммиты сообщением. Иначе работу начать заново с eb6c2c96.
- `claude/fnv-l5-platform-w6` — отправлена по ошибке, указывает на старого предка головы (2593ccd4), нового в ней
  нет. Работник корня рецепта остановлен лимитом до первого коммита.

### Открытые вопросы
- `gate_tests --jobs 2`: `test_recipe.py` красный при параллельном запуске (49/50), один — зелёный; передано L1.
- Частный манифест: fnv-nx-build `l5-platform/manifest` (последний известный 3f220e9; работник regdyn может
  добавить коммит).
