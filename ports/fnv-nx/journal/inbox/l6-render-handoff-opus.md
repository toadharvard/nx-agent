# L6 RENDER: передача лиду Opus (2026-11-11, 22:40Z)

Голова ветки claude/fnv-l6-render на момент передачи — коммит этого файла (родитель 7be9b62d).
Все числа ниже измерены на указанных коммитах. Приказ владельца «один бэкенд, deko3d; gl4/nxgl4/Mesa
уходят из продукта» пришёл в момент передачи: всё про nxgl4 ниже — состояние, не план.

## Влито в claude/fnv-l6-render
- 3fa8b8f1: FMT (правило хранения форматов L8 / L6V5U5 / X8L8V8U8). Приёмка: front, tex, copy, drawgl, gl4,
  ffpdev = ALL OK, 6 из 6 запущенных (не все тесты tests/l6).
- f8545efa: гейт d3d9total (класс 7) в форме транка: 332/332 (161 значение enum + 171 команда SM),
  самопроверка 47 мутаций. Вход: `GATE_OUT=<dir> python3 -I ports/fnv-nx/render/total/gate_d3d9total.py`,
  результат d3d9total.json (fnvnx-gate/1). MojoShader — external в render/total/externals.json
  (fnv-nx-build tools/mojoshader ad5dff84830c2863c841f4b1f4e3df78c705b383, tree_sha256 e49a1b65...6f361).
  L1 принял f8545efa.
- 66e2f971: render/ASSUMPTIONS.md, 129 допущений: OWNER-класс 13, снято гейтом 16, фактом 8, устаревший
  текст 5, REMOVE без гейта 87 (d3d9 57, shaders 15, tests 8, MojoShader/uam 4, total 3). L1 принял, id даёт L1.
- 7be9b62d: render/d3d9/backend/NXGL4_HOST.md (ответ L4 по настоящей libnxgl4; теперь неактуально из-за приказа).
- CLAUDE.md: транк 58a79b3e влит (f5d2519d, новым лидом).

## Очередь
1. НЕ влито: FLAT. Ветка claude/fnv-l6-w-int @ 19c96209 = f8545efa-предок 3fa8b8f1 + слияние
   claude/fnv-l6-w-flat (817a0abd, бит варианта 1 = FNVNX_SHADER_FLAT_COLOR) + QUEUE D5a. Отстаёт от
   головы на 8 коммитов. Приёмка НЕ проведена (воркер остановлен лимитом после пересборки транслятора;
   окружение /home/user/work/l6/ENV_FLAT записано). Не сделано в ней: ps_1_1 `texcoord t0` читает 0.
   После слияния L6A нужен sha для бита варианта 1.
2. 29 меток (заглушки/no-op/отказы) в render/ → 0: реальное тело или тело Wine 9.0 с цитатой. Число 29 —
   из аудита оркестратора (21:34Z); пометочная перепись у меня НЕ готова: воркер claude/fnv-l6-w-recipe
   остановлен лимитом без единого коммита (файла render/CARRY.md нет). Начинать с переписи.
3. render/build.json: render как допущенный корень-продукт рецепта. Компилятор — external gcc-msvcmodel:
   fnv-nx-build tools/gcc-ilp32-msvcmodel b4057704c0babd012e466cc89181132e02542289, tree_sha256
   b60eafe7a5b0ae3dc679f9bf21e20d3363e3bac2af7379e34127ea5cf8455116, cc=bin/aarch64-linux-gnu-gcc,
   cxx=bin/aarch64-linux-gnu-g++; плагины — стадии рецепта; объекты в GATE_CACHE. Не начато.
4. Слияние d3d9total в транк: ждёт от L1 головы транка с пакетами libc6-dev-i386, libclang-common-18-dev в
   env.json. Затем ветка от транка: render/total + входы render/d3d9, render/shaders + строка gates.json
   `{"name":"d3d9total","argv":["python3","-I","ports/fnv-nx/render/total/gate_d3d9total.py"],"result":"d3d9total.json"}`
   + строка admit `{"path":"ports/fnv-nx/render/total","class":"tool","gates":["pure","selftests"]}`
   + записи oracle.json для test_*.py → MERGER; L1 — коммит транка и число гейтов.
5. Двойники в тестах (приказ «no doubles»): перепись в работе, ветка claude/fnv-l6-w-doubles, файл
   render/DOUBLES.md; на момент записи число НЕ получено. Оркестратор ждёт глобальное число (файлы,
   символы, коммит) и путь к 0.
6. L1 ждёт по допущению №80: для uam, транслятора шейдеров, Wine — репозиторий, коммит/версия, есть ли
   зеркало в fnv-nx-build tools/*, и что из них нужно, чтобы корень render собирался. Не отвечено.
7. Удалить 5 устаревших формулировок (класс DROP в render/ASSUMPTIONS.md). Не сделано.
8. Дефект: 8 мест в gl4_*.c используют гостевой адрес данных без проверки guest_ptr = NULL
   (NXGL4_HOST.md, конец). С уходом gl4 из продукта — проверить тот же класс в backend/nx у L6A.
9. render/d3d9/front/gen_enums.py линкует и запускает `g++ -m32` (нарушает compile-only). Не начато.
10. QUEUE.md «D5a accepted»: открыты п. 2 (clip planes / point size, нужен ABI с L6A), 4, 6; маска флагов
    shcache.
11. L6A решает по X1–X6 (громкие отказы T5, claude/fnv-l6-w-dkimage @ 446a37af; T6 claude/fnv-l6-w-dkshader
    @ 07a8d986): сертификат недостижимости или реальное тело.

## Воркеры
Мои воркеры — подагенты этой сессии (не отдельные сессии), id сессий у них нет; после моей остановки
недоступны. Остаются ветки:
- claude/fnv-l6-w-int @ 19c96209 — FLAT-интеграция, см. п. 1.
- claude/fnv-l6-w-recipe — пусто (= 3fa8b8f1).
- claude/fnv-l6-w-doubles — перепись двойников, см. п. 5.
- claude/fnv-l6-w-nxgl4doc @ 5b3d77ab — содержит коммит с трейлером Co-Authored-By (нарушение воркера);
  в основную ветку взят только файл чистым коммитом. Ветку не вливать.

## Публикация
Последняя пара в fnv-nx-build: l6-render/bridge/13c4573c49d7 и l6-render/nxgl4/13c4573c49d7 (nxgl4: commit
2f0427f556c6a1d5af80d76a313a386abfc6c03a, tree_sha256 436df068...62b15). С 13c4573c до 7be9b62d в
render/d3d9/backend и render/shaders изменений 0.

## Окружение контейнера (не переносится)
/home/user/work/l6: ENV_READY, ENV_FLAT, build-flat/{host,deps}, toolchain-1fc1f7b1ef44, trunkwt и
gate7trunk (транк bab699b9). Клон fnv-nx-build: /home/user/fnv-nx-build.

## Открытые вопросы
- Что из tests/l6, завязанных на libnxgl4 (GL-модель), остаётся после приказа «один бэкенд»: по правилу
  «no doubles» граница GPU — только машина L4.
- Первый запуск ELF у L4: отчёта о падении/логе ко мне не приходило.
