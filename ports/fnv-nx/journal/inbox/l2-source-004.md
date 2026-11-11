# l2-source-004 — очистка от заглушек и эвристик в каталогах L2; остановка до 12:30 UTC (2026-11-11)

Ветка `claude/fnv-l2-source`. Хэш набора правил prepare теперь 55221ddcaf15ab76 (был 492dabd6): сертификаты
REC/PATCH (certs.json, recon.json) на новый хэш не выданы — нужен сертифицирующий прогон на ферме.

## Сделано (числа до → после)

- D2: `patches/certs.json` в дереве (668 из 892 мест, набор 492dabd6); после удаления MT1 — 665 ключей.
- D3, 4 дефекта инструментов: закрыты. compile.sh выводит CC из CXX и отвергает другую сборку; reccert
  pristine-overlay копирует tools/, `stamp --overlay` сверяет юниты отчётов с наложением; временные файлы
  equiv/unitcache — mkstemp; ожидание ключей удалено вместе с ключами.
- Порядок памяти: один режим R9 TSO-MAP. Удалены mode=volatile/off, ключи, discover, POLL-PLAIN, слабые счётчики,
  uninit=zero, keys_job.sh, keys_inputs.sh, keys_merge.py. fnvnx_order.cc 1 315 → 598 строк. Проход тотален по
  видам операторов (агрегаты в аргументах, возвраты, блочные builtin по fnspec); неизвестный вид — ошибка компиляции.
- `__builtin_trap` в подготовленном тексте: 343 → 0 (свежее дерево на 80655ee).
- Заглушки в shim/плагинах/патчах: 9 → 0. SEH и `__debugbreak` — объявления без тел с атрибутом
  `fnvnx_lifted("seh"|"int3")`, вердикт Π по атрибуту; MT1, fnvnx_refcount_inc/dec удалены; float→enum — ошибка всегда.
- Inline asm: правило ASM-Π (83 файла, 148 мест: 129 блоков, 19 серий операторов, 8 в #define). Для cl текст
  дословный, для GCC один пустой asm-оператор → вердикт `asm` (lifted). Таблица шаблонов и ручные переводы
  fnvnx_asm.h удалены (271 → 52 строки).
- Плагин Π: `inlined` по графу вызовов (хук удаления узлов, inline-рёбра, блоки), замкнут транзитивно;
  непотреблённая запись — ошибка компиляции; `argorder` покрывает вызов, который order не переставляет.
- Правила prepare: зарегистрировано 117 → 85 (+5 POST-хуков, было 8). Удалены пишущие stand-in (STAND1-6, VTBL1,
  IDTOR1, DTORB1, HKRO1, PHSLOT1, INIT1, CSX2, DTOR1, EMPTY1, STLB1/2) и правила по спискам мест (UNIT1, SPEC1,
  SPEC2, VFTBL1, HUNIT1, XC2, CSX1, THIS2, NATIVE1, BIND1_REFS, VLIT1_BINDS). Записей «по месту» в prepare.py,
  s1_compile.py, s2_bodies.py: ~101 → 0. s1_compile.py 6 027 → 4 344, s2_bodies.py 1 492 → 927, s4_stl.py удалён.
- cl по дереву 80655ee (до удаления STAND, локально, не закончено): 2 416 ok, 351 fail из 2 767 обработанных
  (всего 2 988); было 2 142 / 2 988 на 72e16641.

## Не сделано / открыто

1. Замер «после»: сколько юнитов собирают GCC и cl без удалённых правил — прогон фермы l2r снят (лимит); после
   12:30 UTC послать L4 актуальный sha.
2. Правила, решающие по написанию имён декомпиляции (класс ii, 7 правил): GOTO1, GOTO2 (`hkReal`), LINK1
   (`DAT|PTR|g_`), STR1 (`str_<addr>`), STATIC1 (`DAT|g|UNK|PTR_<addr>`), VLIT1/2 (`DAT_<addr>` + угадывание адреса
   по значению литерала), HARN1 (`fnv_<addr>`). Документирован в декомпиляции только `FUN_<addr>`. Удалить или
   заменить фактом из units.json.
3. STAND7 оставлен (прячет тело за `#ifdef _MSC_VER`, ничего не пишет); VFPAD1 без списка; HARN_KEEP_NAMES —
   5 точек входа платформы. Проверить после замера.
4. Устаревшие id удалённых правил: tools/equiv/equiv.py (UNIT1, CSX1, SPEC1, SPEC2, VFTBL1, HUNIT1, XC2, STLB1,
   STLB2, THIS2, NATIVE1), tools/census.sh:67-69, tools/rules/native1.py, docs/LINK.md, docs/EQUIVALENCE.md,
   docs/PATCHES.md, docs/PERF.md (MT1).
5. Чужие каталоги ещё ссылаются на удалённое: tools/pipeline/build.py:1907-1917 и :132, tools/farm/link_job.sh:73-81,
   tools/link/layout.py:73 (ORDER_KEYS/ORDER_MODE), toolchain/game/boot.cpp:413-446 (fnvnx_mt1).
6. Юниты на C (zlib) компилируются без плагинов: вердикта Π и упорядоченных обращений нет → их функции lifted.
7. Для L1 (D5): нативное тело с вердиктом ≠ ok может ссылаться на объявленные без тел `fnvnx_seh_*` и
   `__debugbreak` — такие тела в линк не входят; строгий линк (0 неопределённых) это и проверяет.
8. D4 (cl-совместимость), D6 (3 юнита GCC), R7 от дампа cl, реестр лемм правил, сводки L-ARG/L-HEAP — не начаты;
   D1 (сертификация → перекомпиляция → cl-дерево → namegate/argordergate) — после пп. 1-2.
9. toolchain/msvcmodel/build.sh требует имя `aarch64-linux-gnu-g++` без версии в PATH (на машине только с `-13`
   проверка списка include даёт rc=3 при исправном компиляторе).

next: после 12:30 UTC — sha для L4, замер юнитов, затем п. 2 и D4/D6 по списку ошибок сборки.
