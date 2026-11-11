# L6 → L6A: передача кадра deko3d (2026-11-11)

Голова на момент передачи: `claude/fnv-l6-render` = f70516fe. Дальше по deko3d L6 ничего нового не начинает.

## Сделано (слито в f70516fe, тесты `python3 -I tests/l6/<n>/test_<n>.py --no-mutations` → ALL OK)
| пункт | файлы (render/d3d9/backend/) | тест | состояние |
|---|---|---|---|
| T0 пин deko3d 373bfd43 (dekotools c15523ce), факты | toolchain/deko3d/build.sh, nx/dk_facts.py, nx/dk_facts.tsv (649 строк) | — | слито; зеркало fnv-nx-build `tools/deko3d-373bfd43d8b2` |
| T1 форматы 35/35 | nx/dk_fmt.c/.h | dkfmt | слито |
| T2 состояние и сэмплеры | nx/dk_state.c/.h (NxDkDerived) | dkstate | слито (мной после слияния не перезапускался) |
| T3 общие проверки аргументов | nxgpu4_check.c/.h | nxcheck, gl4, drawgl | слито |
| T4 ядро, swapchain, PRESENT, WAIT_VBLANK | nx/dk.h, nx/dk_core.c, nx/dk_swap.c, nx/build_dk.sh (→ libnxdk.a, undefined.tsv) | dkcore (двойник-регистратор, ASan+UBSan, аудит линковки) | слито |
| самопроверка первого кадра | selftest/ (nxgpu4_selftest.c/.h, selftest_expect.h, ff_vs/ff_fs.glsl, build_dksh.sh, firstframe_main.c, build_nro.sh) | selftest | слито; GL-эталон FNV-1a 64: 1280x720 0deb9ea57f541461, 1920x1080 c5d3f6436a58bf05 |

## Воркеры по deko3d (Sonnet, локальные worktree в контейнере L6; передаются результатом на ветках)
- T6 шейдеры + патч deko3d blend: ветка `claude/fnv-l6-w-dkshader`. Патч уже закоммичен локально (a1892ba9: `dkCmdBufBindBlendStates` FuncAlphaDst брал dstColorBlendFactor вместо dstAlphaBlendFactor, src/gpu_3d_state.cpp ~130; патч применяет toolchain/deko3d/build.sh; из dk_state убрана NXDK_E_BLEND_ALPHA_DST). dk_shader.c + tests/l6/dkshader в работе. L6 доводит до пуша и сливает в claude/fnv-l6-render; sha пришлю.
- T5 образы/сэмплеры/readback: dk_image.c, dk_sampler.c, dk_xfer.c (+ правка dk.h), в работе, не закоммичено. Воркер допушит в `claude/fnv-l6-w-dkimage`; сливает уже L6A. База воркера 045c2031 (до DKSTATE) — при слиянии возможен конфликт в dk.h.
- Новых воркеров (stream/vtx/pass/blit/query/present) L6 не запускал. Заготовка общего брифа: в контейнере L6, текст ниже в «Нарезке».

## Не начато (T7–T8)
- dk_stream.c: nxdk_submit, nxdk_wait_frame, таблица 28 операций NXDK_OP_LIST, инлайн-операции как в gl4_stream.c.
- dk_vtx.c (DefineLayout/4, BindVtx/N), dk_pass.c (BeginPass/4, Clear), dk_blit.c (Blit, GenMips, resolve), dk_query.c (QueryBegin/End, QUERY_RESULT), dk_present.c (nxdk_present_compose: гамма + курсор).
- Сборка firstframe.nro: firstframe_main.c написан под предполагаемый контракт, его надо привести к dk.h: viInitialize + nwindowGetDefault до `nxdk_create(w,h,err,errlen)`, определить `nxdk_guest_ptr`, `nxdk_on_present`. Требование оркестратора: один .nro, запуск из hbmenu, результат в sdmc:/switch/fnv/firstframe.log (+ .bmp или контрольная сумма), без сети; выложить в fnv-nx-build `l6-render/nro/<sha>` (теперь, видимо, `l6a/...`), инструкция владельцу в 3–4 шага.

## Нарезка, которую я собирался раздать (по файлам, без конфликтов)
Контракт между файлами: вынесенные обработчики имеют форму GL — `void nxdk_op_<snake>(const uint32_t* a, uint32_t argc)` (зеркало `gl4_op_<snake>`); команды пишутся в nxdk.cmd, отправляет только dk_stream.c; ошибки — через тот же приёмник, что у GL (nxgpu4_check / nxdk.lastErr); у каждого файла свой заголовок nx/dk_<file>.h, dk.h никто кроме лида не правит. Воркеры: (1) stream, (2) vtx, (3) pass+blit, (4) query+present.

## Открытые пункты
1. BGR10A2 как цель рендера: deko3d не выражает, правило не выбрано.
2. Флаги устройства OriginUpperLeft + YAxisPointsUp + DepthZeroToOne: возможен двойной переворот Y; front face CW не проверен. Проверяется только на консоли (D18: эмуляторов Switch нет).
3. Регистрация модуля 10 (nxgpu) в toolchain/host для ELF — не сделана; NRO первого кадра автономен.
4. Сторона GL не менялась с `l6-render/nxgl4/8b20825ad72b` кроме рефакторинга проверок (не переопубликовано).
5. Кто запускает NRO на консоли — вопрос у владельца (через оркестратора).

## Что остаётся за L6
D3D9-слой (гостевая сторона, мост `l6-render/bridge/*`), GL-бэкенд gl4_*.c, nxgpu4.h/nxgpu4_shader.h (ABI: менять только по согласованию L6↔L6A), nxgpu4_check.c. За L6A: render/d3d9/backend/nx/dk_*, selftest/, toolchain/deko3d/, tests/l6/dk*, tests/l6/selftest.
