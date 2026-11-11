# L6 RENDER: передача лида (2026-11-11, ~19:00Z)

Ветка `claude/fnv-l6-render`. Всё запушено; воркеров нет (два остановлены в момент передачи, их файлы в ветке как WIP — см. §5). В clean ничего не сливалось. Подробности по каждой задаче и все открытые пункты — `render/d3d9/QUEUE.md` (разделы от 2026-11-11), история нот — `l6-render-005.md`.

## 1. Состояние и числа

| Что | Число | Где мерить |
|---|---|---|
| Методы таблицы интерфейсов (503 = 497 + 6 валидатора) без тела | 0 | гостевая сборка: «L6 method without body» нет |
| Символы моста nxb_* | 514 из 514 | `render/d3d9/build_guest.sh` → defined_nxb.txt |
| Неопределённые от L6 в гостевом архиве | 0 (остаток чужой: 9 хуков платформы, fnvnx_wine_fixme, fnvnx_import_key, fnvnx_guest_invoke, nx_env_profile, libc/libm) | undefined.tsv |
| Гейт d3d9total | КРАСНЫЙ: enum 161/161, команды SM1-3 0/171 | `GATE_OUT=<dir> python3 -I ports/fnv-nx/render/total/gate_d3d9total.py` |
| Транслятор SM1-3, строк вселенной не ok | 0 из 7 538 | `tests/l6/test_sm_total.py --build DIR` |
| Записанные байткоды оригинала | 765/765 транслируются; 33/33 пары VS/PS линкуются на llvmpipe | QUEUE «R1c», «S2a», «B2c» |
| Бэкенд GL 4.5 (ABI v4 + шейдеры D3D9) | полный, libnxgl4.so собрана на ферме gl1 | fnv-nx-build `l6-render/nxgl4/b0be70a6f2d6` |
| Бэкенд deko3d | НЕТ (не начинать, слово оркестратора) | старый v3 в истории: `git show c76e07e6^:ports/fnv-nx/render/native/host/nxgpu.c` |

Тесты (все `python3 -I tests/l6/<имя>/test_<имя>.py [--no-mutations]`, зелёные на последнем прогоне лида): spec, wrap, cmp, dev, res, tex, copy, devlife, present, draw, ffpdev, m1, oom, bridge, exports, front, env, gl4, drawgl, x1, shcache, sm_link (--build). Окружение: `LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe EGL_PLATFORM=surfaceless FNVNX_SHADERS_DEPS=<build>/deps FNVNX_SHADERS_BUILD=<build>/host FNVNX_TC=<prefix тулчейна>`. Не проверено после остановки воркеров: pvw (тест не дописан), прогон под санитайзерами (не закончен). `tests/l6/ffp_ps`, `ffp_vs` красные давно (ссылаются на удалённый старый бэкенд).

Контейнер (пропадёт вместе с сессией; восстановимо): тулчейн fnvnx-a64 — fnv-nx-build `hostcache/toolchain-1fc1f7b1ef44` (распаковать, `FNVNX_TC=…/toolchain/prefix`); сборка транслятора — `FNVNX_MOJO_URL=<fnv-nx-build> FNVNX_UAM_URL=<fnv-nx-build> render/shaders/build.sh` (зеркала `tools/mojoshader`, `tools/uam`; meson > 1.3.2 из pip); пакеты: g++-multilib, g++-mingw-w64-i686, libegl-dev, libgl-dev, mesa, lib32asan/ubsan. Извлечённые шейдеры записей и кэш лежали в /home/user/work/l6 — НЕ опубликованы (пересобрать: `tests/l6/shcache/run_real.sh`, записи — fnv-nx-build `l6-render/d9rec/bb5e5ec8/*`).

Артефакты fnv-nx-build: мост `l6-render/bridge/9d06aa4eed1c` (последний опубликованный: PIE, 514 nxb, ProcessVertices там ещё = завершение процесса; после него в ветке: валидатор константно инициализирован de628869, WIP ProcessVertices); библиотека стадии `l6-render/nxgl4/b0be70a6f2d6`; записи `l6-render/d9rec/*`; зеркала `tools/mojoshader`, `tools/uam`, `tools/mojoshader_assembler`.

## 2. Решения владельца и интерфейсы

- Правила CLAUDE.md в силе (правила, закрывающие класс; без заглушек). Поверх них, 2026-11-11: **«Wine 9.0 как допустимая реализация»** — где Wine 9.0 реализует API упрощённо, тело делает ровно то же + одна строка лога `fnvnx_wine_fixme(id)` (id — строка verify/wine_fixme.tsv у L4: 2 SetClipStatus, 3 GetClipStatus (вид u32nz1), 20 Direct3DShaderValidatorCreate9 (addr _ZL5g_mem+8), 22-24 Begin/Instruction/End валидатора; следующие id бери с 4 или с 25, строки шли L4 одной нотой). Источник — код Wine 9.0 по функции, скачанный, не по памяти. Константы контракта/среды (D3DPERF, ComposeRects, AddRef/Release валидатора, DebugSetMute) — в `render/d3d9/dev/contract_consts.tsv`.
- MEMSAFE (проверяемое подмножество) и wasm-стена транслятора ОТМЕНЕНЫ. Вместо этого: тесты под ASan+UBSan, числа — L4.
- Гейты возврата render в транк (L4, verify/VERDICT.md): objstub, linkstrict, determinism, oracle, d3d9total. В транке render сейчас нет (L4 удалил всё без гейтов); при `git merge origin/claude/fnv-nx-clean` каталоги удалятся — держи свои (`git checkout HEAD -- ports/fnv-nx/render` до коммита слияния).
- **L1** (линк ELF): берёт архив из fnv-nx-build `l6-render/bridge/<голова12>`; имя архива не менять; объекты только `-fPIE`; слабых ссылок и abort нет. Ему нужна новая ветка моста после каждого изменения символов. Даёт: fnvnx_import_key, fnvnx_wine_fixme (loader/include/fnvnx_reach.h).
- **L5** (платформа): 8 функций `render/d3d9/dev/platform_hooks.tsv` + os_exit_process + nx_env_profile — сданы (его ветка 1669b452 и новее, -fPIE 4f074549); bridge.tsv `l5-platform/manifest` 634e4af содержит мои 514 строк — при изменении строк (gen_bridge.py → d9bridge_rows.tsv) сообщай голову. Смена дока: он шлёт WM_DISPLAYCHANGE и перечитывает профиль; мой адаптер читает профиль один раз при Direct3DCreate9 — не решено.
- **L3a**: входы диспетчера для строк `!<Интерфейс>` из bridge.tsv; `fnvnx_guest_invoke(addr, ecx, nargs, args, kind, out)` — только на потоке гостя, без удержанных блокировок, гость может выйти раскруткой.
- **L4** (стадия эмулятора A12, Unicorn): грузит libnxgl4.so через ctypes; `nxgl4_ext(fn, args, nargs)`; от него `nxgl4_guest_ptr` (контракт в gl4.h, RW, одно отображение, правило нулевого размера) и `nxgl4_on_present(frame)`; кадры `$NXGL4_FRAMES/frame_%06u.rgba`; его сторона verify/bootgpu.py готова на поддельной библиотеке. B5 (виртуальные часы, сторона Wine) пишет он; vblank в gl4 сейчас модель 60 Гц по CLOCK_MONOTONIC — под виртуальными часами должен читать их.
- Мост: гость держит «лицо» (8 байт перед объектом), слоты = ключи L1; все указатели интерфейсов конвертируют порождённые переходники (`gen/gen_bridge.py`, `dev/face.h`). Гостевая сборка требует собранный uam (FFP-программы → DKSH).

## 3. Очередь по приоритету (путь к главному меню)

1. ProcessVertices «как Wine 9.0» — довести WIP (§5), тест pvw, убрать os_exit_process, перевыложить мост, сообщить L1 и L4 (у него это находка T).
2. Первый запуск ELF со стадией L4: всё, что вскроется на CreateDevice → первый Present (ждать его отчёта; мои вехи: первый вызов модуля 10, первый nxgl4_on_present).
3. Состояния, не донесённые до бэкенда путём отрисовки (QUEUE «D5a»): D3DRS_LASTPIXEL=TRUE на линиях (умолчание D3D9; сейчас submit = BAD_CALL — любая отрисовка линий падает), пользовательские плоскости отсечения и размер точки программируемых VS, репликация L8 и поправка bump-форматов, плоская закраска переведённых программ. Подключить `nx_m1_draw_legal` в draw_api (решить).
4. Прогон под ASan+UBSan до конца (§5), числа L4.
5. Константные тела «как Wine» из contract_consts.tsv (SetDialogBoxMode и др.) → fnvnx_wine_fixme со строками L4.
6. Часть SM гейта d3d9total (заголовок со статическими проверками в патче MojoShader; гейту нужен клон как объявленный вход); рецепт сборки для admit.json; возврат render в транк.
7. Кэш шейдеров: формат не обслуживает GL (нет GLSL, ключ без VS_COMPLETE) — расширить, пересобрать, опубликовать `l6-render/shcache/<id>`.
8. B5 со стороны рендера (сравнение потоков), V1a (конформанс-тесты Wine), deko3d (B3) — НЕ начинать до слова оркестратора.

## 4. Открытые вопросы и ловушки

- Лимит: оркестратор разрешал 2 воркера (экономия окна); 5-часовой лимит аккаунта уже дважды обрывал воркеров — их возобновляют SendMessage после сброса.
- Воркеры пишут в одно дерево: хук требует коммитить их незаконченные файлы («WIP checkpoint»); тесты, собирающие dev/*.cpp по списку каталога, краснеют от чужого файла в работе — перепроверяй после сдачи.
- `.gitignore` скрывает `*.map`: libnxgl4.map добавлен `git add -f`; проверяй `git status --ignored` на новые файлы продукта.
- Решения воркеров, не сверенные с эталоном (QUEUE: разделы D4, D7, N1, M1, D5a): Reset меняет число буферов; GetLastPresentCount; Present на NULL-формате; три значения состояний по Nine вопреки Wine; защёлка ZFUNC/CULLMODE/FILLMODE/SHADEMODE вне диапазона с придуманным началом; posfix = (0,0,1,0) против текста README шейдеров; табличный туман по MS, а не Wine; патчи = INVALIDCALL (Wine: D3D_OK). По правилу «как Wine» часть из них теперь решается в пользу Wine — пересмотреть.
- L-варианты шейдера (неканонический набор связей) транслируются на первой отрисовке пары — открыто у L4, отложено до первого кадра.
- Ограничения бэкенда GL: StretchRect в MSAA-цель и сжатый↔несжатый → INVALIDCALL; 8x MSAA и 3D BC4/BC5 не создаются; depth больше colour-целей отвергается.
- Утечка записи при раскрутке гостя из fnvnx_guest_invoke в SetPrivateData — правила нет.
- Present с курсором выделяет память на каждый вызов; ссылка на Wine про порядок курсора не сверена.
- В res_smcheck.cpp остался неиспользуемый возобновляемый сканер (от первой попытки X1) — тесты зелёные, убрать или оставить.
- tests/l6 линкуются с `--unresolved-symbols=ignore-all` в части наборов — для гейта linkstrict продукта это не годится (нужен рецепт строгой сборки).

## 5. Воркеры на момент передачи

Оба остановлены лидом, результаты НЕ приняты:
- **PVW (ProcessVertices как Wine 9.0)**: `render/d3d9/dev/pvw_{api,core,light,math,stream}.cpp`, `tests/l6/pvw/` (cases.py, gen.py, oracle.py, t_*.cpp; test_pvw.py ещё нет). pv_exit.cpp уже без ProcessVertices. Гостевая сборка на этой голове проходит (514/514, моих неопределённых 0), test_dev зелёный; сам алгоритм не проверен. Бриф задачи: перенос wined3d_device_process_vertices / process_vertices_strided (исходник: raw.githubusercontent.com/wine-mirror/wine/wine-9.0/dlls/wined3d/device.c), оракул — независимая модель на python в том же порядке операций float32, FIXME-пути Wine → fnvnx_wine_fixme (id с 10).
- **SAN (ASan+UBSan)**: `tests/l6/san.py` (переключатель NX_SAN), `tests/l6/san_summary.py`, правки сборочных функций тестов; таблицы SANITIZERS.md нет; воркер дошёл до sm_link. Чисел нет.
