## L5 PLATFORM — передача дел преемнику (2026-11-11)

### 1. Состояние и числа
- Ветка `claude/fnv-l5-platform`, голова 884b7815 (всё запушено). Заметки inbox 001–011; следующая — 012.
- Частный `toadharvard/fnv-nx-build`, ветка `l5-platform/manifest`, голова 2ac3091 (локально: рабочее дерево
  `/home/user/fnvb-manifest`, каталог `manifest/`). Stop-hook про «5 незапушенных коммитов» там — ложный
  (устаревшая tracking-ссылка; `git ls-remote` равен локальной голове).
- Гейты (последний полный прогон на 7967978d; 884b7815 меняет только `toolchain/guest/os_guest.c`, он в хост-тесты
  не входит, после него пересобран только архив):
  - `tests/s4`: 477 тестов / 404 725 проверок / 0 провалов; `private` 477 / 1 843 / 0;
    `vclock` 1 387 / 0 (прогон на ec2d6bfd, позже не перезапускался);
  - `tests/s7`: 84 / 3 237 / 0;
  - архив `platform/build_guest.sh`: rc=0; неопределённые: newlib/libgcc/libstdc++ 141, рантайм L3a 18, хуки 14,
    UNRESOLVED 0;
  - `platform/manifest/check_hostapi.py` (в цели `test` s4): 35 служб, 51 использование, расхождений 0;
  - ASan+UBSan: в `platform/` 0 находок в s4 и s7 (проверка утечек выключена; `vptr` выключен в s7).
- `bridge.tsv` (частный, 634e4af): 1 552 строки — sym 1 450, lookup 67, l3 32, data 3, none 0. В том числе 454
  системных имени (14 через GetProcAddress), 514 строк d3d9 от L6, строки COM платформы.
- Прочие артефакты манифеста: `import_escape_guest.tsv` 933 строки (столбцы escape n/r/e, writes n/y);
  `import_escape_posix.tsv` устарел; `reach_requests.tsv`; `sys_names.tsv`; `d9bridge_rows.tsv`;
  `combridge_rows.tsv`; `closure.tsv`, `clockdeps.tsv`, `dynsites.tsv`, `filemap_sites.tsv`, `imports.tsv`.
- ENV-TRUTH: строки usp10 / ddraw / mss32 / d3d9d названы оркестратором как «нет» и стояли в очереди после 14
  функций GetProcAddress; закрыты ли они — в этой сессии не подтверждено, преемнику проверить по заметкам 009–011.
- Первый ELF исполнялся в эмуляторе (L1/L4, `l4-verify/boot/509809b3b809`): загрузчик отобразил 4 модуля, связал
  импорты, дошёл до `init d3dx9_38.dll`, затем UnhandledExceptionFilter → ExitProcess(0xC000001D): у модуля нет
  поднятого тела (пакеты модулей L3a). Архивов Data в прогоне не было. Больше ничего из платформы на цели или в
  эмуляторе не исполнялось.

### 2. Решения и интерфейсы
- **L1** владеет `toolchain/include/fnvnx/hostapi.h` и ключами импорта (`fnvnx_import_key`). У нас заголовок L1
  (24b132e6) дословно плюс помеченный блок «L5 ADDITIONS»: служба 35 `FNVNX_PLAT_THREAD_SELF`, n=0. L1 обещал внести
  35 и тексты результатов сокетов / TZ_AT и прислать sha: тогда взять заголовок целиком, удалить блок, перезапустить
  `check_hostapi` и гейты. Запрошено у L1 и без ответа: NO_SERVICE → 36 и тест бэкенда; правка `vm_ext` в
  `section_test.c`; объявлять ли `FNVNX_EXT_GPU` в `hostapi.h`.
- Службы: 1 CPU_BOOST, 2 THREAD_ACTIVITY, 3 MESSAGE, 4–8 VM (VM_RELEASE n=1), 9 DISPLAY_DOCKED,
  10 ACCESS_FAULT_HANDLER (не обслуживается), 11–14 SECTION, 15 UNMAP_FIXED, 16–33 SOCK_*, 34 TZ_AT, 35 THREAD_SELF.
  Модуль 10 = GPU (`platform/include/fnvnx_render_hooks.h`). Литеральные номера в `ext_call` запрещены проверкой.
- **L5** владеет `bridge.tsv` (генератор `platform/manifest/bridgetable.py`). При каждом перевыпуске ключи
  сдвигаются — уведомлять L1 и L3a. Рецепт перевыпуска — в сводке команд ниже.
- **L3a**: рантайм поднятого кода (18 неопределённых символов архива — его), `fnvnx_guest_invoke`; мы даём
  `fnvl_host_world_stop/resume` (`platform/os/os_world.{c,h}`).
- **L6**: d3d9 (514 строк моста), 8 хуков `nxd3d_*` с нашей стороны.
- **L4**: прогоны в эмуляторе, фермы для задач дольше 10 минут. Сборка на виртуальных часах:
  `FNVNX_CLOCK=virtual FNVNX_TC=<prefix> platform/build_guest.sh <out>`.
- Архив собирается `-mabi=ilp32 … -fPIE -ftls-model=local-exec` (по просьбе L1).
- Часы VC1: единственный интерфейс `platform/include/fnvnx_clock.h`; примитивы ОС только в `platform/os/os_clock.h`.
- Журнал: типизированный `W32Log` (`.s() .u() .h() .p() .emit()`); вариадический `w32_log` удалён.
- Гостевой бэкенд берёт страницы только через службы 4–8; запасной путь через кучу удалён.
- Слово владельца о паритете с Wine 9.0: где Wine 9.0 ничего не делает и возвращает успех, делаем так же, одна
  строка журнала на вызов и ссылка на код Wine (`/home/user/gd/wine`). Применять на пути загрузки.
- Остановлено оркестратором до первого кадра: сертификаты reach (0 из 3) и признак «импорт не пишет». Подмножество
  MEMSAFE приостановлено (только санитайзеры).

### 3. Очередь по приоритету
1. Путь к главному меню: разбирать каждую остановку из прогонов L4/L1 после точки входа exe (следующий прогон ждёт
   пакеты модулей L3a). Названные оркестратором кандидаты: предопределённые классы окон, файлы/архивы, DirectSound.
   Игра импортирует из user32 для окон только CreateWindowExA / RegisterClassA / UnregisterClassA / MessageBoxA;
   нужны ли BUTTON/EDIT — зависит от имён классов в местах вызова, не проверено.
2. Ввод имени: сочетание кнопок открывает swkbd. Программная клавиатура и отображение контроллера на клавиши для
   MessageBox не начаты.
3. Принять новый `hostapi.h` от L1 (п. 2).
4. После первого кадра: правило инструмента для GetProcAddress (принято L1: `ptrtoint`, все использования
   которого — сравнения с константами, и использование значения в ветви `< 0x10000` → непрозрачная функция
   nocapture/readnone; условие корректности — в ветви `≥ 0x10000` целое дальше не используется; сравнение HMODULE с
   адресом образа = сравнение с константой), перевыпуск таблицы; переименовать идентификаторы reach в имена
   `reach_facts.h` L1 и удалить `reach_local.h`; сертификаты reach.
5. Отложено: перевод рецепта `fnvnx-recipe/1` для L4 и первый столбец пути единицы в `env_const.objstub.tsv`;
   разбор 132 находок IKOS; пробелы B2 и шрифта B3; устаревшая posix-таблица import_escape.

### 4. Открытые вопросы и ловушки
- Не проверено исполнением: пути остановки мира на Horizon и в госте (svcSetThreadActivity + цикл по
  svcGetThreadContext3 — допущение); хост-файлы L1 и `h_thread_self` / `pf_thread_self` здесь не компилировались
  (нет aarch64-linux-gnu-gcc и qemu).
- `runtime.c` у нас и у L1 расходится структурно; версия L1 не взята, сверка за L1.
- `claude/fnv-nx-clean` не содержит дерева платформы (конфликты delete/modify); слияние туда — дело MERGER по
  зелёному вердикту L1.
- Пробелы B2: CopyFileA не переносит mtime; меню и полосы прокрутки отсутствуют; флаги GCP; кучи и стеки вне карты
  страниц; write-watch против записей платформы; SendMessage между потоками ждёт вечно, если получатель не
  возвращается; GetFileAttributesExA отсутствует (не импортируется).
- Значения W27, не установленные документацией: порядок обратных вызовов FLS (по Wine), LUID/дескриптор D3DKMT
  выбраны, GetFontUnicodeRanges только BMP.
- import_escape: гостевой бэкенд считает clang-18 с целью `arm64_32-apple-watchos` — заявленное допущение.
- Ловушки прогона:
  - вывод `make -C tests/s4` писать в файл и смотреть rc: grep по конвейеру скрывает падение процесса;
  - перед коммитом удалять `tests/s4/registry.hiv`, `tests/s7/registry.hiv`;
  - `ports/fnv-nx/ext` — git-игнорируемая ссылка на `/home/user/gd/ext`, в каждом рабочем дереве воркера создавать
    заново;
  - санитайзеры: вывод через `| cat`, `ASAN_OPTIONS=detect_leaks=0:halt_on_error=0:handle_segv=0:
    allocator_may_return_null=1`, для s7 `-fno-sanitize=vptr`.
- Команды (из `ports/fnv-nx`):
  - `make -C tests/s4 -j4 > /home/user/gd/s4.log 2>&1; grep -E ' FAIL$|tests, ' /home/user/gd/s4.log | tail -2`;
    `make -C tests/s4 private`; `make -C tests/s4 vclock`; `make -C tests/s7 -j4`;
  - `FNVNX_TC=/home/user/gd/toolchain/prefix platform/build_guest.sh /home/user/gd/out-lead`;
  - мост: `M=/home/user/fnvb-manifest/manifest; make -s -C tests/s4 -j4 probe;
    tests/s4/build/probe_imports < $M/sys_names.tsv > probe.tsv;` собрать `defined_symbols.txt` через `nm
    --defined-only -g` по `tests/s4/build/platform` и `tests/s7/build/platform`; `cp
    platform/com/combridge_rows.tsv $M/; python3 platform/manifest/bridgetable.py $M/sys_names.tsv
    /home/user/gd/wine/specs $M/bridge.tsv $M/defined_symbols.txt probe.tsv $M/d9bridge_rows.tsv
    $M/combridge_rows.tsv`; коммит и `git push origin HEAD:l5-platform/manifest`.
- Локальные каталоги контейнера (в новой сессии их не будет): `/home/user/gd` (префикс тулчейна, дерево Wine 9.0,
  `ext`), `/home/user/fnv-nx-build`.

### 5. Воркеры
- Работающих нет. W25, W27, W28, W29, W30 завершены и слиты. W31 (рецепт `fnvnx-recipe/1`) остановлен, ничего не
  слито. Лимит оркестратора: не более 2 воркеров одновременно.
