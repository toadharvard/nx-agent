## J-XXXX: L5 PLATFORM — B1 по счёту тел закрыт, очистка от заглушек в platform/ (2026-11-11)

### Числа (голова 6621757d)
- `tests/s4`: 341 тест / 343 006 проверок / 0 провалов. `tests/s7`: 63 / 2 321 / 0.
- bridge.tsv (fnv-nx-build `l5-platform/manifest` d453017): 945 строк = 440 манифеста + 8 экспортов d3d9.dll + 497
  методов COM d3d9 (L6). Без тела: 50 → 0. sym 839, lookup 71, l3 32, data 3.
- Линт L4 `nostub` (старый, токенный; текстовые правила позже отозваны) по platform/, до → после:
  trap/abort 18 → 0; weak/alias 11 → 0; пустые тела 12 → 0; константные тела 51 → 0 в исходниках
  (98 строк таблицы `platform/manifest/env_const.tsv`, тела порождает `gen_env_const.py`).
  Осталось: 8 конструкторов со списком инициализации (правило снято L4), 2 находки в `platform/build_guest.sh`
  (`set +e`, `|| true`).
- Архив для ld: `platform/build_guest.sh` — 94 исходника, 0 сбоев, 1 836 символов; гейт неопределённых КРАСНЫЙ:
  12 символов BSD-сокетов (исполнитель W8b работает).

### Сдано после ноты 009
| Задача | Что |
|---|---|
| W4 | шрифтовой gdi32 этап 2: `text` 7/7, `extentex` 7/7, `placement` 7/7, `caps` 1/1 эталонного дампа равны побайтно |
| W5 | одна карта страниц: VirtualAlloc/Free/Protect/Query, IsBad*Ptr, отображения файлов; `fnvnx_win32_vm_fault` |
| W7 | одно состояние дисплея (ENV D1): GetSystemMetrics, ChangeDisplaySettingsA, EnumDisplayDevicesA, `nx_env_profile` |
| N1 | audio + input без заглушек: типизированные интерфейсы фильтров DirectShow |
| N2 | win32/crt/os без заглушек: квартиры COM, write-watch, ini по контракту (подстановка значений порта удалена), `os_fatal` и weak-символы удалены, захват мыши и ClipCursor, образ TEB |
| W6 | `platform/build_guest.sh` + `platform/guest_hooks.txt` |

### Контракты
- L3a: обратные вызовы гостя только через `fnvnx_guest_invoke`; исключения платформы — `fnvnx_win32_set_raise_backend`;
  сбой страницы — `fnvnx_win32_vm_fault`. Через `w32_raise` и обратные вызовы нельзя держать блокировки.
- L1: ключи — `fnvnx_import_key`; поток — `fnvnx_thread_block_create` + `fnvnx_lift_enter`; образ —
  `fnvnx_win32_register_image`, секции из `fnvnx_start_proc()->map[]`; hostapi: VM_PROTECT=8, DISPLAY_DOCKED=9, сокеты 16–33.
- L4: гейт objstub на объектах (T/V/L), таблица констант в виде `symbol/kind/value/contract_ref`; гейт imports.
- L6: строки d3d9 и COM в bridge.tsv; `nx_env_profile`.

### НЕ сделано (очередь, по порядку)
1. Сокеты гостя (W8b в работе): гейт архива до 0 неразрешённых.
2. Платформа НЕ вызывает хуки L1/L3a: `GetProcAddress` отдаёт нативный указатель, `CreateThread` стартует поток
   по-старому, обратные вызовы (WNDPROC, старт потока, qsort, фильтр исключений) зовутся как нативные,
   `fnvnx_win32_register_image` не написана. Без этого lifted ELF на платформе не работает.
3. Гейт imports для L4 (не начат; формат гейта L4: `{"format":"fnvnx-gate/1","name",…,"status",…}`).
4. Выгрузка таблицы констант в формат objstub; рецепт `platform/build.json`.
5. Тела vararg (sprintf/_snprintf/sscanf) с гостевым списком аргументов — для модулей.
6. COM dsound/dinput гостю «лицом» и строками bridge.tsv, как у L6.
7. Гостевая сторона виртуальных часов VC1.

### Открытые пробелы (частичные реализации, B2)
- Кучи и стеки потоков вне карты страниц; write-watch не видит записи, сделанные платформой за гостя.
- Именованные отображения не делят пространство имён с событиями; отображение файла не согласовано с WriteFile.
- GetCharacterPlacement: флаги шейпинга не действуют; таблица Bidi по блокам; DC без межсимвольного интервала.
- GetSystemMetrics: часть метрик записана по памяти о Wine, не сверена с исходником (архив Wine 9.0 есть в контейнере).
- `os_display_docked` в госте читает отсутствие службы хоста как «портатив» — fallback, убрать.
- Steam RunCallbacks: рассылка зарегистрированным объектам при открытом канале не имеет тела (канала по ENV M6 нет).
- Значения порта для Fallout.ini (bUseThreaded*) больше нигде не задаются: должны прийти из ini, который поставляет загрузчик.
- Цели `m32` и `ilp32` тестов не собирались (нет lib32/кросс-компилятора в контейнере); бэкенд Horizon не компилировался.
- `toolchain/guest/os_guest.c` всё ещё определяет удалённые из os.h `os_fatal`, `os_init`, `os_tls_*`.
- Источники 6 строк таблицы констант (пины DirectShow) записаны по памяти — сверить с MSDN.
- Тест пишет `registry.hiv` в текущий каталог.
