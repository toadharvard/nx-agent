# l6-render-w4 (2026-11-12)

Ветка claude/fnv-l6-render-w4 от транка a2453fe9; гейт d3d9total (класс 7) входит в транк.

Файлы (все из claude/fnv-l6-render f8545efa, без изменений; только данные для гейта, продуктовые C не собираются):
- render/total: gate_d3d9total.py, externals.json, TRUNK.md, tests/test_d3d9total.py (+ tests/oracle.json новый).
  gate_l6.py и census/ не взяты: гейт их не читает.
- render/d3d9/front/* (10 файлов: .c читаются гейтом как данные, -fsyntax-only, switch-покрытие), render/d3d9/backend/nx/state.h
  (единственный файл backend, который front включает; остальной backend гейт не читает), render/d3d9/spec/d3d9_spec.json.
- shim/include/d3d9types.h, fnvnx_win32.h (замыкание include пробы -m32).
- render/shaders/total/{sm_table.json,gen_sm_rows.py,fnvnx_sm_rows.h}, render/shaders/{mojoshader.patch,build.sh}.
- MojoShader остаётся внешним: tools/mojoshader ad5dff84, tree_sha256 e49a1b65.
Изменено: verify/gates.json (+d3d9total; render/total в argv pure и selftests), verify/admit.json (+render/total tool pure,selftests;
render/d3d9, render/shaders, shim: class doc, gates [] - это входные данные гейта, не продукт; корни продукта появятся с рецептами),
verify/tests/test_gates.py (ожидание списка гейтов и каталогов; правило гейтов не менялось).

Числа: гейт 332/332 (161/161 enum, 12/12 перечислений чувствительны; 171/171 SM, мутаций не убито 0), 4 s.
Самопроверка: 47 мутаций, 47 убито, база 332/332, 70 s.
Вердикт l1.py на 57a810ff: green, 24 гейта, 268.0 s (env: env.json транка уже содержит libc6-dev-i386 и libclang-common-18-dev;
setup_verdict_env.sh -> envcheck OK). Красных нет. Первый прогон на 8549517b: красный только selftests (test_gates.py
ожидал старый список гейтов), исправлено.
claude/fnv-l1-contract-w16 на origin не найдена, скретч-слияние не делалось (пины уже в транке).

## После слияния транка e35b870a (2026-11-12)
Слияние origin/claude/fnv-nx-clean e35b870a в ветку: a23ca9ad. Конфликты gates.json и test_gates.py разрешены объединением
(imports, machine, d3d9total; test_gates.py ждёт объединение), правила гейтов не менялись.
Вердикт l1.py на a23ca9ad: green, 26 гейтов, 238.0 s; pure 39/39, selftests 56/56, admit 459/459, oracle 56/56, d3d9total 332/332.

Класс doc для render/d3d9, render/shaders, shim (в admit.json есть только doc, tool, product; класса «входные данные гейта» нет):
replaced when render/d3d9 (L6), shim/include (L2) and the translator root land as product roots.
