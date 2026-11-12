# l5-platform-w9-006: crt_symbols.tsv удалён целиком, мёртвые нативные crt-тела удалены

Ветка claude/fnv-l5-platform-w9, поверх origin/claude/fnv-l5-platform 41a38a10.

Правило: crt_symbols.tsv удалён (4546 строк, все привязки по имени). Нативное crt/cpplib-тело живёт, только если на него есть ссылка
из таблицы экспортов DLL (msvcrt_names.tsv -> msvcrt_table.gen.cpp, exports.cpp) или из остальных объектов platform/ (граф ссылок по nm,
пересчитан до неподвижной точки: удаление -> сборка -> nm -> удаление). Остальное мертво и удалено.

Числа (платформа, 41a38a10 -> эта ветка):
- platform/crt: удалено 2216 строк кода (6763 с tsv), добавлено 3. Файлов удалено целиком: cpplib.cpp, helpers.cpp, math.c, fnvnx_msvc_cpp.h, crt_symbols.tsv (5).
  Частично: iob.c, string.c, stdio.c, heap.c, fnvnx_msvcrt.h, fnvnx_iobuf.h.
- глобальных определений в objects platform/crt: 278 -> 101 (удалено 177 нативных тел и данных, в т.ч. C++ exc<>/locimp/facet).
- остаются как import-тела: 101 глобальный символ (msvcrt_exports.cpp 52 c_*, iob/string/heap/stdio/exitlist/msvcrt_format: то, на что есть ссылка из таблицы экспортов или platform/).
- Ты назвал fnvnx_mutex_lock/unlock, fnvnx_fiopen, fnvnx_basic_ios_init, fnvnx_ios_base_addstd: удалены (cpplib.cpp целиком).
- Читатели удалены: bodies.py load_provider_params + load_providers(addrmap) + ветки CRT_MUTEX/CRT_FACET в crt_rule/emit_crt + _table;
  fnvlink.py load_providers + параметр/опция --crt-symbols + цикл crt_classes (providers остаётся как карта import-thunk'ов: это факт импорта);
  seamlayout.crt_starts; prepare.crt_inline_float + _GAME_FLOAT_RET; pipeline/undef.load_crt и категории code_crt/code_eh/code_nobody.
- tests: s4 497 -> 478 тестов, 0 упало (удалены test_cpplib.cpp, test_iob.cpp, test_nolock.cpp целиком; в test_crt -9, test_fs -1,
  test_boundary -1, test_msvcrt_exports -2 тестов; test_plat без fopen/_open; test_saves пишет через WriteFile). s7: 89 тестов, 0 упало.
  s2d/test_bodies: 44 теста (-4: provider_params, exe_provider_shape, facet, streambuf_lock), 1 ошибка test_tree_inline_copy_rename была и на базе.
- Потеря покрытия, честно: живые экспорты fwrite/fread/fflush/_fileno/_lseeki64/_write из таблицы теперь без теста: файл для них нечем
  создать (fnvnx_iob_fopen удалён), старый test_msvcrt_exports::msvcrt_iob_and_lowio их проверял через него.

fnv-nx-build: l5-platform/manifest 3e1a14aa9882f8c8a380cba0ca13642e68af4765, tables/ только imports.tsv (210 строк; imports.tsv на 41a38a10:
SteamUtils правка lead'а учтена), tree_sha256 cad6c278793104e23dacd6e55178e4b53d5d4ff933f38a2a810516a6989576c4; crt_symbols.tsv там удалён тоже.
Пин tools/tables.pin.json обновлён. imports.tsv: строки IAT-слот, dll, api, units, имена decomp, provider. Это импортные имена exe (факт импорт-таблицы)
плюс адрес слота и имена decomp, поэтому перенос остаётся.

Другие слои (не тронуто):
- shim/include/fnvnx_crt.h, fnvnx_iobuf_fopen.h, fnvnx_equiv_compat.h, tools/equiv/msvc/*, tools/prepare.py (DECL1-FOPEN, GAME1 CRT-прототипы): объявляют/мапят на удалённые
  fnvnx_iob_fopen, strcpy_s, memcpy_s и т.п. (L2).
- tools/rules/s4_stl.py (fnvnx_fiopen, fnvnx_basic_ios_init, fnvnx_xlength_error), tools/rules/s1b_patches.py + tools/equiv/equiv.py (fnvnx_ftol2),
  tools/difffuzz/{engine,libcalls,arm64emu,causes}.py, tools/pipeline/undef.py (имена sprintf_s...), platform/manifest/bridgetable.py (fnvnx_msvcrt_va_*), startup/guest/onexit.c (_onexit):
  ссылаются на удалённые имена (L2/L3a/L4).
- tools/link/test_slotfill.py: убраны kp/_purecall-проверки; числа vtmetric (trap-slots 9 / 19 и т.д.) не пересчитаны, нет aarch64-инструментов здесь. Пересчитать на машине с ними.
- docs/RUNTIME.md: таблицы адрес-имя (строки около 258-260 и далее) не перенесены, docs/ не в моём объёме.
- check_hostapi (hostapi-check, make -C tests/s4): ACCESS_FAULT_HANDLER и TEXT_ENTRY без хоста — падает независимо от этих правок, запускал s4 через make -o hostapi-check test.
