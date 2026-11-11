# L6 RENDER 006 — ProcessVertices и D5a по Wine 9.0, интеграция (2026-11-11)

| Что | Число |
|---|---|
| Голова nx-agent `claude/fnv-l6-render` с собранными исходниками | 8b20825a |
| Мост fnv-nx-build | `l6-render/bridge/8b20825ad72b`, nxb 514/514, неопределённых от L6 0 |
| libnxgl4.so | `l6-render/nxgl4/8b20825ad72b`, ABI v4, экспорт как у b0be70a6f2d6 |
| Тесты L6 | 23/23 зелёные (`l6-render-006-tests.tsv`) |
| pvw | ALL OK, 79–90 с; мутации 27/27 пойманы (приёмка лидом: rc=0) |
| Расхождения ProcessVertices с Wine 9.0 | 8 закрыто, не закрытых 0 |
| fnvnx_wine_fixme id | 2, 3, 10–15 (pvw_fixme.tsv), 40 LASTPIXEL (d5a_fixme.tsv); следующий свободный 41 |

- undefined.tsv моста относительно 9d06aa4eed1c: убран `os_exit_process`; добавлены `cosf`, `expf`, `powf`, `sqrtf`.
- UBO драйвера VS (binding 2) 64 → 208 байт: мост и libnxgl4 берутся парой одной головы.
- Строки 10–15 и 40 у L4 в `verify/wine_fixme.tsv` видом `branch` (claude/fnv-l4-verify 22e4eb6).
- Удалены устаревшие тесты `tests/l6/ffp_ps`, `tests/l6/ffp_vs`.
- Открыто: первый запуск ELF на стадии L4 (ждёт пак L1); L8/bump-форматы и плоская закраска FFP-VS + транслированный PS; санитайзеры; тела-константы → wine_fixme (в работе); SM-часть гейта d3d9total 0/171 (в работе).
