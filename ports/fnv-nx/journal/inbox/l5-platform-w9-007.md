# l5-platform-w9-007: Legal, таблицы адрес→имя из публичного дерева

- docs/RUNTIME.md: 5 таблиц (480 строк с VA) перенесены в fnv-nx-build l5-platform/manifest, tables/runtime_doc_sections.md (commit 348f67b0, tree 3780fd5e…). В публичном доке правило и ссылка на tools/tables.pin.json. Комментарии dshow.cpp, mmio.cpp очищены от VA.
- Перепись публичных текстовых файлов с VA рядом с именами: до 683 файлов / 14442 строк, после 683 / ~13950 (platform+docs чисты).
- Остаток принадлежит другим слоям: patches/*.s1b (69 файлов, 7262 строк), units_renames.tsv (1409), tools/equiv/explained.tsv, tools/rules, tools/difffuzz, tests/*, journal, shim, toolchain. Платформенные tools/perf (hot_rows.json, budget_model.json, mtmodel.py, xthread_audit.py) и check_regsites.py: данные ждут решения лида (перенос с читателем через tools/tables.py).

## Дополнение (perf, пин)
- budget_model.json, hot_rows.json, xthread_defaults.json (константы DEF_* xthread_audit.py) перенесены в fnv-nx-build tables/ (commit 0f3f8dd7, tree 50dc1737…); читатели (hotspot_metric, frame_budget, content_presets, mtmodel, xthread_audit, 4 теста) берут через tools/tables.py (FNVNX_TABLES, sha-пин). Адреса в комментариях mtmodel/xthread_audit убраны.
- check_regsites.py: адреса синтетического образа, остаются (комментарий добавлен).
- Тесты perf с unicorn/elftools в контейнере не запускаются (нет модулей); проверены: regsites selftest 21 rows, test_xthread all ok, hotspot_metric работает на пине.

## test_slotfill vtmetric (verified env: setup_verdict_env.sh, envcheck OK)
- До удаления: 52+2 ok / 0 failed (d52fe657^). После: 13/2 и 19/12. Построчная разница (vtmetric --rows, обе ссылки): 4 новых trap-no-native на exe-адресе 0x004010e0 (бывший _purecall): Vk слот 3, Pv слот 0, копия Bf слоты 0 и 1; в варианте без fill Vk слот 3 из native-at-exe в no-native. Ожидания в тесте исправлены с выводом в комментарии: 54 ok, 0 failed.
- tests/v1/test_a57_perf_prices.py: KeyError per_call_cycles в p1 — и до моих правок (данные budget_model.json те же sha256 da12363f…); не моё, лиду.
