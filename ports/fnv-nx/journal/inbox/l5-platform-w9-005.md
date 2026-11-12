# l5-platform-w9-005: таблицы адрес↔имя ушли в fnv-nx-build

- Перенесено: platform/crt/crt_symbols.tsv (4546 строк), platform/win32/imports.tsv (210 строк). Удалены из nx-agent.
- fnv-nx-build, ветка l5-platform/manifest, каталог tables/, коммит a949bb74950d831b179db8fc5c2d06fb02b0a51b, tree_sha256 045faae47d4cacee525ac94fd1578e1dec4bac69426d42ae507b400d5ba25921.
- Пин в дереве: tools/tables.pin.json (commit, tree_sha256, sha256 каждого файла). Читает tools/tables.py: каталог из FNVNX_TABLES, сверка sha256, иначе выход 2. Умолчаний и путей к /home/user/fnv-nx-build нет.
- Ключи сборки (build.py, farm/link_key.sh) берут хэш пина вместо файлов.
- Тесты без данных: tests/imports_synth.tsv (синтетика). test_s2c_xexit, test_a57_perf_prices и s15 `list` без --exe читают через FNVNX_TABLES.
- Перепись (на 1b4a7b7c): 8 файлов-таблиц / 6493 строки + 68 патчей .s1b / 2623 строки. Вне platform/ (не перенесены, для других слоёв): patches/units_renames.tsv, tools/equiv/explained.tsv, tools/equiv/classes.tsv, tests/gates/fixtures/.../abi.tsv, tests/s4/backends/backends.tsv, patches/*.s1b, плюс упоминания в прозе.
- Известное: tools/link/test_w28 падает на D3DXSaveTextureToFileA и на базе (нет в exports.cpp).
