# l6-render-008 (2026-11-11)

- Ветка claude/fnv-l6-render @ f8545efa.
- FMT (правило хранения форматов) влито: 3fa8b8f1. Приёмка: front, tex, copy, drawgl, gl4, ffpdev = ALL OK (6/6).
- Гейт d3d9total (класс 7 вердикта) в форме транка: влито 9676cf54 -> f8545efa. Вердикт 332/332
  (161 значение enum + 171 команда SM), самопроверка 47 мутаций. Вход:
  `GATE_OUT=<dir> python3 -I ports/fnv-nx/render/total/gate_d3d9total.py`, результат d3d9total.json (fnvnx-gate/1).
- MojoShader объявлен внешним входом (render/total/externals.json): fnv-nx-build tools/mojoshader ad5dff84,
  tree_sha256 e49a1b65...; несовпадение = красный.
- L1 принял f8545efa. Слияние в транк ждёт от L1 головы транка с пакетами в env.json.
- Не влито: FLAT (claude/fnv-l6-w-flat @ 817a0abd), интеграция в работе (claude/fnv-l6-w-int).
- Открыто: 29 меток в render/ -> 0; render/build.json (компилятор gcc-msvcmodel b4057704);
  перечень допущений для verify/ASSUMPTIONS.json (claude/fnv-l6-w-assume, в работе);
  front/gen_enums.py линкует и запускает g++ -m32 (нарушает compile-only).
- Последняя опубликованная пара bridge+nxgl4: 13c4573c49d7.
