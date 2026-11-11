# L6A DEKO: передача дел преемнику (2026-11-11)

Ветка `claude/fnv-l6a-deko`, голова кода `60d5aa86`. Всё закоммичено и запушено.
Ветка не предок транка (расхождение ~1990 коммитов). Транковый CLAUDE.md `58a79b3e` (раздел «late night»)
влит в ветку коммитом `4335c142` (не мной, во время передачи).

## Состояние продукта

- Код: `ports/fnv-nx/render/d3d9/backend/nx`, 18 файлов .c; deko3d закреплён на `373bfd43d8b2`.
- Таблица границы: `render/d3d9/backend/dkabi.tsv` (`08252f17`): 74 строки = 67 deko3d + 7 libnx
  (eventClear, eventClose, eventWait, nwindowGetDefault, viCloseDisplay, viGetDisplayVsyncEvent,
  viOpenDisplay). Счётчики мест вызова (206 мест): fnv-nx-build `l6a/dkabi` @ `071b497`.
- NRO `l6a/nro/796aac6c150a` (fnv-nx-build) отозван. Новый NRO на консоль — только после зелёного `dkall`.
  Эмуляторы Switch запрещены (D18).

## Гейты (старая форма, с дублёрами; хост, ASan/UBSan)

Запуск из `ports/fnv-nx`: `. /home/user/work/l6a/env.sh && python3 -I tests/l6/<n>/test_<n>.py`
(окружение `/home/user/work/l6a` было в моём контейнере, у преемника его нет — пересобрать).

| коммит | гейты | итог |
|---|---|---|
| `60d5aa86` | dkpresent (364 проверки, 76 мутаций), dkmsres, dknro | ALL OK |
| `0868d5cf` | dkdraw, dkmsres, dkscale, dkpresent, dkcore, dknro, dkstream (185 проверок, 81 мутация), dkpass | ALL OK |

На `60d5aa86` НЕ перезапускались: dkdraw, dkscale, dkcore, dkstream, dkpass, dkblit, dkimage, dkquery,
dkshader, dkvtx, dkstate, dkfmt (изменение 0868d5cf→60d5aa86 — только dk_present.c и его тест).

## Дублёры в тестах (приказ «no doubles»): 12 харнессов из 12, 137 символов продукта

Измерено на `4c542643`, структура с тех пор не менялась. Цель — 0.

| харнесс | переопределено символов продукта |
|---|---|
| dkblit | 8 |
| dkcore | 23 |
| dkdraw | 11 |
| dkimage | 2 |
| dkmsres | 10 |
| dkpass | 6 |
| dkpresent | 12 |
| dkquery | 3 |
| dkscale | 9 |
| dkshader | 2 |
| dkstream | 44 |
| dkvtx | 7 |
| **итого** | **137** |

Путь к 0: каждый харнесс — гостевой ELF (AArch64 ILP32) под `verify/boot.py`, все 18 файлов продукта
линкуются настоящие, ниже — одна общая модель L4. Первым — `dkall`; попросить L1 добавить `dkall` в gates.json.

## Контракт с L4 на модель deko3d (согласован до приказа «no Mesa»)

- `libdkmodel.so` экспортирует: `dkmodel_create(w,h,err,n)`, `dkmodel_destroy`, `dkmodel_ext(fn,args,nargs)`,
  `dkmodel_last_error`, `dkmodel_inject(fn, ordinal, dk_result)`.
- Встраивающая сторона даёт: `dkmodel_guest_ptr(addr,size)`, `dkmodel_on_present(frame)`.
- `fn` = порядковый номер некомментарной строки `dkabi.tsv` (с 1); `fn 0` = «забрать ошибку из очереди cbError».
- L4 пишет `verify/bootdk.py`; запуск `boot.py run --dk LIB [--inject FILE]`. Образец: транк `verify/bootgpu.py`.
- **Изменено приказом:** Mesa нет; модель L4 исполняет DKSH сборки. План «sha256(DKSH)→GLSL, GL 4.5 llvmpipe» отменён.

## Очередь

Сделано и влито в ветку:
- DKTMP, DKPRESMS — временные образы и MS-present.
- DKNEUTRAL `7a56e734` — `nxdk_draw_neutral`, `nxdk_ms_resolve_on` (dk.h, dk_draw.c, dk_msres.c, dk_scale.c).
- DKPASSEND — `pass_state_reset()` в начале SUBMIT и после ожидания очереди (dk_stream.c); dk.h S6 `0868d5cf`.
- DKPRESON `60d5aa86` — dk_present.c: `pc_resolve` через `nxdk_ms_resolve_on`, без записи в `nxdk_s.cmd`.

Остановлено при передаче (результата нет, ветки без коммитов сверх базы):
- DKM1 — ядро модели и генератор из dkabi.tsv. Его задание требовало GL/EGL Mesa — устарело («no Mesa»).
  Перерезать: генератор thunk'ов из dkabi.tsv + гостевая сторона; исполнение — у L4.
- DKREADMS — dk_read.c через `nxdk_ms_resolve_on`, удалить `resolvable()`/X4. Начать заново от `60d5aa86`.

Не начато:
1. dk_blit.c: отказы S7 → `nxdk_ms_resolve_on` + `nxdk_tmp_image`.
2. 12 харнессов → гостевые ELF на общей модели L4 (см. выше), dkall первым.
3. Стадия рецепта `uam` для DKSH.
4. Пересборка и публикация firstframe NRO — только после зелёного dkall.
5. Слова «assumption» в nx/* (было ~14, часть убрана DKNEUTRAL/DKPRESON; пересчитать на голове) → id реестра или факты.
6. Решение по X1–X3 (dk_image.c).
7. backend/nx на транк как product root рецепта (msvcmodel-компилятор, плагины, GATE_CACHE).
8. `nxdk_scale_point` / `nxdk_target_bind` (void) сообщают об отказе только через `nxdk.errCount` — нужен контракт.

## Работники

Это были локальные Agent-субагенты в worktree моего контейнера, не облачные сессии: session id у них нет,
их ветки локальные и НЕ запушены (контейнер эфемерный — считать утраченными). Всё принятое влито
cherry-pick'ами в `claude/fnv-l6a-deko`.

| задача | agent id | локальная ветка | статус |
|---|---|---|---|
| DKNEUTRAL | a15c3624e294ffde8 | claude/fnv-l6a-dkneutral @ b46fe3fc | влито 7a56e734 |
| DKPASSEND | a29f9088aa7a12a37 | claude/fnv-l6a-dkpassend @ e37c1398 | влито |
| DKPRESON | aedd57d576d6a0782 | claude/fnv-l6a-dkpreson @ 84aa5648 | влито 60d5aa86 |
| DKTMP | aeda133d94d74fb2b | claude/fnv-l6a-dktmp @ f04da801 | влито |
| DKPRESMS | aa8c2894fa08fa3ac | claude/fnv-l6a-dkpresms @ 591c6f1c | влито |
| DKALL (разведка) | a16290afc891375e2 | claude/fnv-l6a-dkall @ c2668383 | не для транка |
| DKM1 | a2e6a30a063a613f5 | claude/fnv-l6a-dkm1 @ 08252f17 | остановлен, пусто |
| DKREADMS | a7ab224d75cf4b0ea | claude/fnv-l6a-dkreadms-w @ 60d5aa86 | остановлен, пусто |

DKALL-разведка: хост-драйвер всех 18 файлов с промежуточной моделью-дублёром (поэтому не для транка).
22 отказа до правок → 12 на `0868d5cf`. Оставшиеся 12 («present draws into the swapchain image: 0») —
ошибочная посылка драйвера: present рисует в `nxdk.front`, затем копирует в swapchain. Исправленным
прогоном это НЕ подтверждено. Ошибка продукта `pass_srgb: no open pass` на стыке 18 файлов исчезла.

## Открытые вопросы

- `verify/ASSUMPTIONS.json` на транке `c9765781` отсутствовал — запись решения по шейдерам через L1.
- От L6 жду: список маркеров stub-гейта (file:line); sha для бита 1 варианта шейдера = FLAT_COLOR; render/ как корень рецепта.
- L4: id ext-модуля для dk (выдаёт L1); семантика vblank/времени для `eventWait`; кто исполняет DKSH в модели
  после «no Mesa» и в каком виде продукт передаёт DKSH модели.
