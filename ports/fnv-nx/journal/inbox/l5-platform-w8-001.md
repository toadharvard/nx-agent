# l5-platform-w8-001: DirectShow без заглушек

Ветка `claude/fnv-l5-platform-w8`. Макросы `FNVNX_UNIMPL` и `E_NOTIMPL_` удалены (определения убраны,
`grep` по ports/fnv-nx в коде и тестах пуст; остались упоминания только в старых journal/inbox).

## Что было и во что стало

| метод | было | стало | источник поведения |
|---|---|---|---|
| IMediaControl::AddSourceFilter | E_NOTIMPL | `fnvnx_wine_fixme(200)`, S_OK, out не тронут | wine-9.0:dlls/quartz/filtergraph.c:MediaControl_AddSourceFilter (FIXME stub, S_OK) |
| IMediaControl::get_FilterCollection | E_NOTIMPL | `fnvnx_wine_fixme(201)`, S_OK | wine-9.0:dlls/quartz/filtergraph.c:MediaControl_get_FilterCollection |
| IMediaControl::get_RegFilterCollection | E_NOTIMPL | `fnvnx_wine_fixme(202)`, S_OK | wine-9.0:dlls/quartz/filtergraph.c:MediaControl_get_RegFilterCollection |
| IAsyncReader::RequestAllocator (файловый источник) | E_NOTIMPL | реальное тело | wine-9.0:dlls/quartz/filesource.c:FileAsyncReader_RequestAllocator |
| IAsyncReader::Request | E_NOTIMPL | реальное тело (чтение выполняется в момент запроса) | ...:FileAsyncReader_Request |
| IAsyncReader::WaitForNext | E_NOTIMPL | реальное тело | ...:FileAsyncReader_WaitForNext |
| IAsyncReader::SyncReadAligned | E_NOTIMPL | реальное тело | ...:FileAsyncReader_SyncReadAligned |
| IAsyncReader::BeginFlush/EndFlush | S_OK без состояния | реальное состояние (flushing, сброс запросов, событие) | ...:FileAsyncReader_BeginFlush / _EndFlush |
| ReaderPin::ConnectionMediaType | E_NOTIMPL при подключении | копия типа соединения; не подключён: VFW_E_NOT_CONNECTED + нулевой тип | wine-9.0:libs/strmbase/pin.c:pin_ConnectionMediaType |
| BFilter::QueryVendorInfo | константа `E_NOTIMPL_` (env_const.tsv) | константа `(HR)0x80004001`, источник MSDN + цитата Wine | wine-9.0:libs/strmbase/filter.c:filter_QueryVendorInfo (E_NOTIMPL без FIXME) |

Новое реальное содержимое, нужное для RequestAllocator/Request: `IMemAllocator_` / `IMediaSample_` (IMediaSample2) в
dshow_int.h и классы `MemAlloc` / `MemSample` в dshow_filters.cpp, порт
wine-9.0:dlls/quartz/memallocator.c (BaseMemAllocator_*, StdMediaSample2_*, StdMemAllocator_Alloc/_Free).
Раскладка буфера: шаг = cbBuffer+cbPrefix, округлённый вверх до cbAlign, буфер после префикса (так же
делает SDK-аллокатор; у Wine это FIXME "не выравниваем"). Список запросов растёт (у Wine assert i < max_requests).

`fnvnx_wine_fixme(id)` определена в platform/win32/platstat.cpp (всегда пишет одну строку "fixme: wine-9.0 id N" в sink/os_debug_print
и считает вызов в классе unimpl под именем wine-fixme). Определения этой функции на транке нет: если L1/L6 определили её
в другом месте, при слиянии оставить одно.

## Для L1 (verify/wine_fixme.tsv)
Файл verify/wine_fixme.tsv на ветке = транк + 3 строки: 200, 201, 202 (kind u32, value 0).

## Тесты
tests/s7/test_dshow_async.cpp (новый, 5 случаев): async reader целиком (запрос/ожидание/EOF/частичное чтение/flush/таймаут),
аллокатор (выравнивание, prefix, outstanding, decommit), IMediaSample (все 19 методов + свойства), ReaderPin::ConnectionMediaType,
три метода IMediaControl. Ожидаемые значения взяты из цитированного кода Wine 9.0. Тесты линкуют настоящие файлы платформы.
Тесты DirectShow живут в tests/s7, не в s4 (в s4 только env_const-часть dshow).
В test_dshow_lock/contract константа E_NOTIMPL_ переименована в локальную kNotImpl (моки игровых объектов).
tests/s4/test_plat.cpp вызывает fnvnx_plat_note напрямую вместо макроса.

## Нерешённое
- Wine при Free аллокатора с выданными сэмплами освобождает память под ними (сэмплы остаются с parent=NULL); порт повторяет.
- Wine SetProperties у сэмпла копирует pMediaType указателем; порт повторяет.
- CLSID_MemoryAllocator для CoCreateInstance не зарегистрирован (игре не нужен: аллокатор создаётся внутри RequestAllocator).
