## J-XXXX L3a LIFT: переход на recipe.py — лифтер только генерирует C++ (2026-11-11 21:35Z)

status: wip. Ветка claude/fnv-l3a-lift, голова bf497fc + этот коммит. Для L1 (ждёт эту заметку) и оркестратора.

### Что сделано в этом коммите
- `lift/tools/liftgen.py` — генератор без компилятора. Шард: `texts_K.txt` + `missing_K.tsv` + `forms_K.tsv`. Finish: дерево пака `u/000.cpp … u/fff.cpp`, `disp.S`, `imp.cpp`, `inc/fnvl_prelude.h`, `inc/imp.h`, `entries.tsv`, `forms.tsv`, `imp_*.tsv`, `gate_liftgen.json`.
  - Правило юнитов: вход A лежит в `u/<(A >> 4) & 0xfff>.cpp`, для всех образов. 4 096 + 2 имён фиксированы: список `outputs` рецепта не меняется, изменённый текст функции меняет ровно один юнит.
  - Полнота: finish падает, если у какого-либо образа множество текстов ≠ вселенная.
  - Проверка «символ рантайма определён» из генератора убрана: неопределённый символ рантайма — это неопределённый символ линка, его ловит linkstrict.
- `lift/tools/packcopy.py` — стадия `generated`: копия 4 098 фиксированных имён из external `/ext/liftpack` в `/out`. puregate: 0 нарушений.
- `liftgen.py --recipe REF COMMIT TREE_SHA256` пишет `lift/pack/build.json`: g++-13, флаги лифт-кода из test/instest.py без `-c -DFNVL_FLAGS_IO -DFNVL_STUBX`, `include: /ext/liftpack/inc`, `units: all`, `link: null`, один external (ветка пака в fnv-nx-build). Плагины fnvnx_order/fnvnx_ftoi к этому корню не применяются (решение оркестратора 21:22Z).
- `lift/tools/farm_liftgen.sh`, `farm_packgen.sh` — публикация шардов исходников (`l3a-lift/src/<sha12>[-<module>]/<K>of<N>`) и дерева пака (`l3a-lift/pack/<sha12>`). objcache/linkcache и свой пин numpy в них отсутствуют.

### Проверено локально (один модуль, libvorbisfile)
| что | число |
|---|---|
| входов / текстов / без тела | 797 / 797 / 0 |
| юнитов | 4 098, самый большой 72 234 байт |
| текст | 2 583 688 байт, дерево пака 19 МБ (в каждом юните один include прелюдии) |
| компиляция | самый большой юнит, imp.cpp, disp.S — без ошибок (локальный g++, не env.json) |

Полный прогон `recipe.py build` (две холодные сборки → один objects.json; тёплая → stages.run = 0) НЕ выполнен: нужен опубликованный пак исходников и ферма с окружением env.json (мой контейнер: 11 расхождений с env.json).

### Корни (план, принят L1 21:23Z)
- `lift/pack` — product, все юниты из external → без плагинов.
- `lift/rt` — product, рукописный рантайм (rt.c, x87.c, seh.c, enter.c, msvcrt_*.c, fmath.c, …) с обоими плагинами: ждёт стадию плагинов L1. Не начато.
- tool: `lift/fnvlift`, `lift/tools`, `lift/test`, `lift/bench`, `lift/proof`; doc: `lift/README.md`.

### Блокеры допуска (не мои)
- гейты `oracle`, `disp` (L1), `forms` (L3b) ещё не написаны → product-корень не допускается;
- linkstrict охватывает все product-корни → lift и platform допускаются одним слиянием;
- стадия плагинов в recipe.py (L1) → нужна для `lift/rt`.

### Старый путь
Пак c7684608b332 (объекты через liftall.py) дособирается по указанию оркестратора: d3dx9_38 18 / 32 шардов на 21:31Z, collect после 32 / 32. liftall.py и farm_liftall.sh / farm_collect_all.sh удаляются после первого зелёного `recipe build`.

### Дальше
1. collect c7684608b332; 2. шарды исходников на фермах по фактам span-правила (143 тыс. входов), пак, build.json; 3. `recipe build` ×2 на ферме, отчёт L1; 4. корень `lift/rt`.
