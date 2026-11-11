# l5-platform-w9-004: переделка пункта A — 75 строк `i` удалены

Прежнее решение (удалить колонку how) отозвано по замечанию лида: строки `i` продолжали задавать привязки.
Правило: привязка exe-функции к нативному телу держится на факте (запись импорта или сертификат идентичности N1-N4), не на имени. Нет факта — строка удалена, функция становится лифтованным кодом.

1. Из platform/crt/crt_symbols.tsv удалены 75 строк, бывших `i` в origin/claude/fnv-l5-platform (4621 -> 4546 строк данных; `n` строки не тронуты; колонки how нет, она удалена в 45cdba64). Провайдеры удалённых строк:
- 0x009fea2a body_unnamed: needs body (decompile)
- 0x009fead6 thunk: fnvnx_nvapi_not_initialized
- 0x009feae8 thunk: fnvnx_nvapi_not_initialized
- 0x009febd8 thunk: fnvnx_nvapi_not_initialized
- 0x00ec31b3 cpplib: fnvnx_ios_base_addstd
- 0x00ec3246 cpplib: fnvnx_mutex_ctor
- 0x00ec3260 cpplib: fnvnx_mutex_dtor
- 0x00ec3277 cpplib: fnvnx_mutex_lock
- 0x00ec3280 cpplib: fnvnx_mutex_unlock
- 0x00ec3289 cpplib: fnvnx_init_locks_ctor
- 0x00ec32ba cpplib: fnvnx_init_locks_dtor
- 0x00ec33da cpplib: fnvnx_fiopen
- 0x00ec34d4 cpplib: fnvnx_locale_getgloballocale
- 0x00ec35d7 cpplib: fnvnx_locinfo_dtor
- 0x00ec36a6 cpplib: fnvnx_locale_init
- 0x00ec3740 cpplib: fnvnx_locinfo_ctor
- 0x00ec379c cpplib: fnvnx_tolower_ctype
- 0x00ec38a9 cpplib: fnvnx_getctype
- 0x00ec3909 cpplib: fnvnx_toupper_ctype
- 0x00ec3a75 cpplib: fnvnx_ostream_ctor
- 0x00ec3bad crt: fnvnx_byteswap_ulong
- 0x00ec3bda crt: fnvnx_byteswap_ushort
- 0x00ec3bf0 crt: fnvnx_byteswap_uint64
- 0x00ec6940 crt: libc: floor
- 0x00ec6cc4 crt: libc: vsprintf
- 0x00ec7230 crt: libc: memmove
- 0x00ec7944 cpplib: fnvnx_exception_copy_ctor
- 0x00ec79a1 cpplib: fnvnx_exception_dtor
- 0x00ec79b7 cpplib: fnvnx_exception_what
- 0x00ec7d50 crt: fnvnx_clock
- 0x00ec8244 crt: platform/crt: _mbscmp
- 0x00ec858a crt: platform/crt: _mbsicmp
- 0x00ec85e3 crt: platform/crt: _errno
- 0x00ec862c crt: fnvnx_rename
- 0x00ec8965 crt: libc: isspace
- 0x00ec8d33 crt: libc: strtoul
- 0x00ec923c crt: fnvnx_stat
- 0x00ec9610 crt: libc: asin
- 0x00ec9774 crt: fnvnx_iob_fprintf
- 0x00ec9d4a crt: fnvnx_iob_printf
- 0x00eca190 crt: libc: acos
- 0x00eca2e0 crt: libc: memchr
- 0x00eca38d crt: platform/crt: sprintf_s
- 0x00eca903 crt: platform/crt: _mbsnbcpy
- 0x00eca920 crt: libc: log
- 0x00ecacb2 crt: platform/crt: _mbsncpy
- 0x00ecad86 crt: platform/crt: wcstombs_s
- 0x00ecaebb helper: fnvnx_eh_vector_copy_ctor
- 0x00ecb849 crt: platform/crt: _mbsnbcmp
- 0x00ecba8e crt: platform/crt: _mbsnbicmp
- 0x00ecbc5b crt: platform/crt: _strupr_s
- 0x00ecbc80 crt: libc: log10
- 0x00ecbe60 crt: fnvnx_iob_vprintf
- 0x00ecc071 crt: fnvnx_wcsicmp16
- 0x00eccbdc crt: fnvnx_getcwd
- 0x00ecd735 crt: fnvnx_iob_fgetpos
- 0x00ecd7a3 crt: fnvnx_iob_fsetpos
- 0x00ecd7e8 crt: fnvnx_iob_fseeki64
- 0x00ecda70 crt: fnvnx_iob_fflush
- 0x00ecdb42 crt: libc: strerror
- 0x00ecddf1 crt: platform/crt: _vsnprintf
- 0x00ecdf2d crt: libc: strtod
- 0x00ecdf44 crt: platform/crt: __iob_func (fnvnx_iob[3], MSVC _iobuf layout)
- 0x00ece23e crt: fnvnx_mbstok
- 0x00ee6d44 thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d4a thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d50 thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d56 thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d5c thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d62 thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d68 thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d6e thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d74 thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d7a thunk: fnvnx_ws2_* (see imports.tsv)
- 0x00ee6d80 thunk: fnvnx_ws2_* (see imports.tsv)

2. Потребители и нативные тела.
- Привязки: fnvlink.load_providers и bodies.load_provider_params читают только таблицу; без строки адрес не перенаправляется. Жёстко зашитых привязок к удалённым провайдерам нет.
- fnvnx_nvapi_not_initialized: был в platform/manifest/env_const.tsv (строка ENV:M2, генерирует win32/env_const.gen.inc) и в комментарии services.cpp. Строка env_const и комментарий удалены, тело не генерируется. Других ссылок нет (тест-фикстур тоже). Тонкие `jmp [slot]` thunk-и exe 0x009fead6/0x009feae8/0x009febd8 теперь лифтованный код.
- Удалены неиспользуемые тела (platform/crt): fnvnx_exception_{copy_ctor,dtor,what}, fnvnx_mutex_{ctor,dtor}, fnvnx_init_locks_{ctor,dtor}, fnvnx_locale_{init,getgloballocale}, fnvnx_locinfo_{ctor,dtor}, fnvnx_getctype, fnvnx_{tolower,toupper}_ctype, fnvnx_ostream_ctor (cpplib.cpp); fnvnx_byteswap_{ushort,ulong,uint64}, fnvnx_mbstok, fnvnx_wcsicmp16 (string.c); fnvnx_getcwd, fnvnx_rename, fnvnx_stat и struct fnvnx_msvc_stat (stdio.c, fnvnx_msvcrt.h); fnvnx_iob_{fgetpos,fsetpos,fprintf,printf,vprintf} (iob.c, fnvnx_iobuf.h) + их объявления. Записи в tools/difffuzz/libcalls.py (fnvnx_byteswap_ulong) убрана.
- Тесты, которые их проверяли, удалены или переписаны на оставшийся интерфейс: tests/s4/test_cpplib.cpp (locale/locinfo/ctype/ios_base/ostream, byteswap, mbstok, wcsicmp16), test_fs (stat/getcwd; rename -> MoveFileA), test_iob (fprintf через vfprintf, fgetpos/fsetpos через fseek), test_saves (rename -> MoveFileA).
- НЕ удалены, потому что у них есть потребитель (не помощник DLL, а L2-слой): fnvnx_mutex_lock/unlock (tools/link/bodies.py CRT_MUTEX: привязка по имени `_Lock/_Unlock` — тот же класс догадок по имени, L2 должен заменить), fnvnx_fiopen и fnvnx_basic_ios_init (tools/rules/s4_stl.py пишет вызовы в исходники), fnvnx_ios_base_addstd оставлен как static внутри cpplib.cpp (его зовёт basic_ios_init), fnvnx_iob_fseeki64 (fseek). fnvnx_clock используется платформой. fnvnx_eh_vector_copy_ctor определён только в tools/difffuzz/a64helpers.cpp (харнесс difffuzz), не в platform. libc/platform/crt-строки (_vsnprintf, sprintf_s, wcstombs_s, _strupr_s, _mbs*, _errno, __iob_func, memmove, strtod и т.д.): тела это msvcrt-экспорты/libc, не удалялись; их привязка исчезла вместе со строкой. ws2: 11 thunk-строк удалены; fnvnx_ws2_* — тела win32/ws2, импортируются DLL, не тронуты.

3. Прогон: s4 497 tests, 0 failed (было 498; объединены/удалены тесты удалённых тел), s7 84 tests, 0 failed. tools/link/test_*.py: 36 ok, 16 FAIL; 14 из них — нет aarch64-linux-gnu-g++/as в контейнере, test_w28, test_decomp_std и tests/s2d/test_bodies (1 error) падают так же на базе без моих правок.

4. Происхождение строк `n`: таблица введена одним коммитом 54d3633b (2026-11-03, S4, «classify the undefined code addresses and imports», docs/RUNTIME.md). Генератора нет: класс/идентификация/провайдер выведены S4 из декомпа (имена функций и их вызовов, 'named' = имя есть в декомпе) на decomp 87eb9373 (build/units.json), дальше править руками (последующие коммиты правили строки in place). Из 4546 строк `n` (теперь все): eh 4238, atexit_dtor 121, crt 81-, body_unnamed 29 и т.д. (awk по колонке class). Это тоже опора на имя декомпа, а не на факт exe; не менялось по заданию.
