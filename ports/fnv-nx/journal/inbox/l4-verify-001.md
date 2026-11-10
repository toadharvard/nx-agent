### J-XXXX | 2026-11-10 | stream: L4-verify | status: wip

Счётчик исполненных инструкций (метрика перфа владельца) и первые факты P0. Только числа.

* **did** - `verify/icount.py` + `tests/s5/emu_guest.py --icount OUT.json [--icount-classes SPEC.json]`: точный счёт по блокам
  (UC_HOOK_BLOCK, size/4), по потокам, по интервалам между вехами (`mark()`; кадры позже тем же вызовом), классы:
  total, ldar/stlr (подсчётчики ldapr, ldaxr/stlxr), dmb, dsb, isb, диапазоны (lifted) и входы (SoftFloat) из SPEC.json
  по точным именам секций/символов. Тест `verify/tests/test_icount.py` 6/6: сумма == пошаговый эталон, маски против 47
  мнемоник, два прогона побайтно равны.
* **measured (ферма s5b1, линк e1ca6b02d60d, null-устройство, данные владельца, 108 с весь job)**

  | интервал | поток | инструкций | ldar/stlr-семейство | dmb |
  |---|---|---:|---:|---:|
  | start -> winmain | main | 356 935 235 | 54 305 | 5 096 |
  | winmain -> ini-read | main | 3 992 512 | 6 500 | 974 |
  | ini-read -> archive-list | main | 10 645 303 | 52 616 | 116 |
  | archive-list -> bsa-opened | main | 33 763 312 | 16 159 | 56 |
  | bsa-opened -> renderer-init | main | 6 645 038 | 4 046 | 64 |
  | renderer-init -> stop | main | 402 157 | 416 | 37 |
  | всего | main / t8 / t9 | 412 383 557 / 1 293 / 25 236 | 134 042 / 8 / 558 | 6 343 / 0 / 44 |

  Всего 412 410 086; ldar/stlr-семейство 134 608 (0,033 %), из них ldaxr/stlxr 110 849; dmb 6 387 (0,0015 %); dsb 0, isb 0.
  Точность: срезов по бюджету 0, застабленных сбоев 1 (блок сбоя посчитан целиком), 48 переключений потоков на host-вызовах.
  lifted и SoftFloat: 0 (классов в линке ещё нет; SPEC.json строится из ELF, имена даёт L3).
* **measured (boot, опыт 3)** - e1ca6b02d60d, null-устройство + данные: 5/10 вех по порядку (winmain, ini-read, archive-list,
  bsa-opened 6, renderer-init); стоп UC_ERR_READ_UNMAPPED в `FindHashEntry` [00af9c50]+0xc0 <- FindArchiveForFile [00af6160] <-
  FileFinder::GetFile <- LoadFromReader <- InitRenderSystem [00b57420]. Остановка в коде архивов, не в рендере. Raw-boot
  full1 на e1ca шёл без данных (bsa 0/0).
* **measured (линк, опыт 1; full1 e1ca6b02d60d, стадия link 835 с)** - resolve 1 185,3 (exe rtti 110,9), probe 136,6
  (vtables 132,7), bodies 173,1, resolve 2 265,7 (exe rtti 202,7), apply 24,8, check 4,7. exe rtti 313,6 с = 38 %; синтез
  таблиц компиляцией 16 %. linkcheck 780 с = шаг base 186 + make link 590.
* **measured (trap, опыт 4)** - guest_undefined: 4 (82fd66d3) -> 11 (b7b866ef..e1ca6b02): -1 +8; 8 новых появились между
  0829588 и c22a8a9 (кандидаты 07bf394, fcac0d2, 7a7afae); причина по символам не установлена.
* **did** - `verify/opentrace.py` (герметичность стадии: чтения под strace ⊆ ключ; чтение вне ключа или состояния прошлого
  прогона = красный), тест 6/6.
* **next** - профиль `exe rtti` и трасса стадии link против её ключа на локальном стенде; L1 дважды на двух головах (lc1);
  базовые числа гейтов (lg1, full1).
