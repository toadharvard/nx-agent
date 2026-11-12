# l5-platform-w9-007: Legal, таблицы адрес→имя из публичного дерева

- docs/RUNTIME.md: 5 таблиц (480 строк с VA) перенесены в fnv-nx-build l5-platform/manifest, tables/runtime_doc_sections.md (commit 348f67b0, tree 3780fd5e…). В публичном доке правило и ссылка на tools/tables.pin.json. Комментарии dshow.cpp, mmio.cpp очищены от VA.
- Перепись публичных текстовых файлов с VA рядом с именами: до 683 файлов / 14442 строк, после 683 / ~13950 (platform+docs чисты).
- Остаток принадлежит другим слоям: patches/*.s1b (69 файлов, 7262 строк), units_renames.tsv (1409), tools/equiv/explained.tsv, tools/rules, tools/difffuzz, tests/*, journal, shim, toolchain. Платформенные tools/perf (hot_rows.json, budget_model.json, mtmodel.py, xthread_audit.py) и check_regsites.py: данные ждут решения лида (перенос с читателем через tools/tables.py).
