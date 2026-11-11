# l5-platform-w9-001: колонка how в crt_symbols.tsv

Случай: колонка `how` (n/i/u) не читалась ни одним решением. Генератора таблицы нет (файл ведётся руками), все
потребители (fnvlink.load_providers, bodies.load_provider_params, seamlayout, undef, prepare) берут колонки 0, 1, 2, provider.
Решение: колонка удалена целиком (заголовок + значение в каждой строке), индексы provider 6 -> 5, фикстуры тестов
(tools/link/test_bindings.py, test_slotfill.py, tests/s2d/test_bodies.py) обновлены.
Числа: строк 4621 (данные), `i` до: 75, после: 0 (колонки нет), `n` 4546. Классы/идентификации строк не менялись.
Проверено: tests/s2d/test_bodies (provider) OK; test_bindings/test_slotfill требуют aarch64-linux-gnu-g++ (нет в контейнере), не запускались.
