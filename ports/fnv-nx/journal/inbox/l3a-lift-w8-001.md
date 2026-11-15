## w8 L3a LIFT: fnvnx_dispatch (DISPATCH1 runtime), item 26 (2026-11-12)

status: готово, тесты зелёные на стенде (msvcmodel из fnv-nx-build `tools/gcc-ilp32-msvcmodel`, плагины из репозитория, Unicorn).

### что сделано
- `lift/rt/dispatch.c`: `fnvnx_dispatch(fp, site, desc)`. Правило: (1) fp — адрес нативного тела: таблица `fnvnx_native_disp_table` (тот же `dispgen.py --prefix fnvnx_native`, строки `адрес<TAB>символ` из `contract/nativerows.py` = bind.json + obj sympair `def` + bodysel `native`; один источник); (2) иначе fp — ключ лифт-диспетчера (`fnvl_has_entry`, новая чистая функция в rt.c): точный поиск моста по FNV-1a(desc) через минимальный перфектный хеш (phf.py); (3) иначе #UD по адресу fp через SEH-рантайм (`fnvnx_guest_invoke` → `fnvl_nobody`). Запись site — один 64-битный store (`__atomic_store_n`), таблицы read-only.
- `lift/tools/bridgegen.py`: один мост на различный дескриптор + таблица; вход — `.fnvnx.disp` объектов (`--obj`) или список (`--descs`). Дескриптор вне грамматики, `this` без `i` первым, скрытый указатель при `fast`, HFA неверного размера, коллизия хеша — ошибка сборки со списком всех.
- Плагин `fnvnx_dispatch.cc`: (a) пишет секцию `.fnvnx.disp` (дескрипторы юнита); (b) HFA несёт тип элемента: `s<N>hf|hd`, возврат `a<N>hf|hd` (`s8h` = {float,float} или {double} — разные регистры); (c) HFA до 4 элементов идёт в FP-регистрах при любом размере (раньше >16 байт давало `r`); (d) перед каждым вызовом пишет fp в thread-local `fnvnx_call_fp`, потому что мост один на дескриптор и сам fp не получает (кэш-попадание не вызывает dispatch).
- `enter.cc`: `fnvnx_guest_invoke_regs(addr, ecx, edx, ...)` (нужен fastcall); `fnvnx_guest_invoke` не менялась.
- `phf.table_asm(prefix)`, `dispgen --prefix/--allow-empty` (пустая таблица нативных тел — все поиски мимо).

### тесты (всё зелёное)
- `lift/test/disptest.py`: настоящие rt.c, dispatch.c, enter.cc, seh.cc, плагины, сгенерированные мосты; native-код с косвенными вызовами компилируется плагинами. 14 случаев из 14: this, fastcall, stdcall/cdecl под одним дескриптором (один сайт, три цели подряд, esp гостя не меняется), q-возврат и q-аргумент, float/double через ST0, результат в памяти (m20), a12 (регистры AAPCS, память x86), a8 (edx:eax), HFA-аргументы s8hf/s8hd/s24hd, HFA-возврат, класс по ссылке s12r, нативное тело приоритетнее лифтового по тому же адресу, #UD по адресу вне таблиц (код ^ адрес в обработчике). «Двойники»: пересечение определённых символов теста и продуктовых объектов пусто (проверяется в тесте).
- `lift/test/bridgegentest.py` (без тулчейна, 70+ проверок), `dispgentest.py` (+3), `contract/tests/test_nativerows.py`.
- Не прогнан: `verify/tests/test_plugins.py` (нужен sandbox; добавлена проверка секции `.fnvnx.disp`).

### открытое (нужны решения лидов, класс без правила — не латаю)
1. Дескриптор исходит только от плагина; в рецепте `generated`-стадия видит лишь отслеживаемые файлы, а `.fnvnx.disp` лежит в объектах других корней. Нужен факт-файл `descs.tsv`, который L1 собирает из объектов (как sympair в obj.json), и корень `lift/bridges` (build.json + admit.json), чей generated-этап запускает `bridgegen.py --descs`. Я этого не делал: без факта не к чему пиновать. Число дескрипторов по всем корням измерить нельзя до первой сборки native-корня.
2. `fast` + результат в памяти: регистр скрытого указателя MSVC не доказан → ошибка сборки (не догадка).
3. x86-правило возврата агрегата: 1/2/4/8 байт в eax:edx, остальное — скрытый указатель (так cl x86 делает; источник — поведение MSVC, не расписано в моих материалах).
4. `nativerows.py`: несколько символов на один адрес (ctor c1/c2) — ошибка, а не выбор. Что делает N1 для ctor c1/c2 — вопрос L1.
5. #UD, который обработчик «продолжает» (ContinueExecution), перевыбрасывается на том же fp (x86: возобновление по тому же EIP). Обработчик, правящий EIP на другой адрес и возвращающийся по сентинелу, из `fnvnx_dispatch` не выразим: возврата функции-указателя нет.
6. `fnvnx_call_fp` — `__thread` (initial-exec): платформа/загрузчик обязаны выделять TLS-блок гостевым потокам; стенд делает это сам.

## Update (corrections of the L3a lead, L2 review)
- Rule 3 (#UD) is withdrawn. dispgen.py --closure F makes a closure target with no entry a build error (2a9138eb). The run-time miss paths (rt/dispatch.c, fnvl_nobody in rt/enter.cc and rt/rt.c) are NOT yet replaced by fnvnx_reach_violation(fact_id): loader/include/fnvnx_reach.h exists only on claude/fnv-l1-contract, and gate_reach.py has no request kind for "every fp reaching the dispatcher is a closure key", so no certificate id exists. Waiting for L1/lead. Any such certificate is valid only after the 12 dynamic GetProcAddress/LoadLibrary sites are resolved (measurement build until then).
- Plugin: hfa() deleted, classes come from the backend (pass_by_reference, function_arg, function_value). Commit af7283ad (for L2), the x flag for non-POD results 
  is a separate commit. Element codes hf hd hq hh hv8 hv16.
- MSVC return rule now from documents: Microsoft "Argument Passing and Naming Conventions" (8-byte structures in EDX:EAX, larger through the hidden pointer, non-PODs never in registers) and Agner Fog "Calling conventions" table 7 (Microsoft 32 bit: PS = hidden pointer on the stack even under fastcall for too-big and non-power-of-2 sizes; P = non-POD, register under fastcall not stated). Under fast a non-POD or `m` result stays a build error (the register is undocumented).
- Tests: lift/test/descclasstest.py (descriptor classes vs the compiler's own prologue/epilogue registers, 20 types, argument and result), bridgegentest, dispgentest, disptest 14/14, verify/tests/test_plugins.py 39 PASS in the declared env.
- Needs from others: L1 descs.tsv fact over .fnvnx.disp of admitted roots; L5 TLS block for fnvnx_call_fp (initial/local-exec).
