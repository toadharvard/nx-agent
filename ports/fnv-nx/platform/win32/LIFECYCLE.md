# Жизненный цикл приложения Switch → оконные сообщения Win32 (L5, правило)

## Правило

Событие Horizon отображается в ту последовательность сообщений, которую по документации Win32 порождает оконный
менеджер Windows для того же действия пользователя над полноэкранным окном. Что с ней делает игра, решают её
собственные обработчики; платформа ничего не знает об игре.

| Событие Horizon (applet message) | Действие-аналог в Windows | Последовательность (в порядке доставки) |
|---|---|---|
| FocusState → OutOfFocus / Background (HOME, оверлей, swkbd не считается: он внутри ввода) | переключение на другое приложение | `WM_NCACTIVATE(FALSE)`, `WM_ACTIVATE(WA_INACTIVE, 0)`, `WM_ACTIVATEAPP(FALSE, tid)`, `WM_KILLFOCUS` |
| FocusState → InFocus | возврат в приложение | `WM_NCACTIVATE(TRUE)`, `WM_ACTIVATE(WA_ACTIVE, 0)`, `WM_ACTIVATEAPP(TRUE, tid)`, `WM_SETFOCUS` |
| сон (всегда после потери фокуса) | переход системы в S3 | `WM_POWERBROADCAST(PBT_APMSUSPEND)` |
| пробуждение | выход из S3 | `WM_POWERBROADCAST(PBT_APMRESUMEAUTOMATIC)`, затем `PBT_APMRESUMESUSPEND`; затем последовательность возврата фокуса |
| ExitRequest (закрытие из HOME) | закрытие окна пользователем | `WM_CLOSE` в очередь окна; `DefWindowProcA` → `DestroyWindow` → `WM_DESTROY`, `WM_NCDESTROY` |
| OperationMode / разрешение изменились (док ↔ портатив) | смена режима дисплея | `WM_DISPLAYCHANGE(32, MAKELPARAM(w, h))`; рабочий стол и `GetSystemMetrics` отвечают новым режимом |

Время: `GetTickCount`, `timeGetTime`, `QueryPerformanceCounter` идут и во сне (как в Windows). Потоки гостя в фоне
не останавливаются платформой: Windows их не останавливает. Horizon останавливает процесс сам при сне — это
эквивалент S3.

Звук: при потере фокуса буферы DirectSound без `DSBCAPS_GLOBALFOCUS` и без `DSBCAPS_STICKYFOCUS` замолкают по
контракту DirectSound (уровень кооперации окна); приёмник audren продолжает работать и получает тишину. При сне
audren останавливается системой и перезапускается при пробуждении; позиция буферов считается по часам звука.

GPU: в фоне Horizon не выдаёт кадры; `Present` блокируется на vsync как при закрытом окне. Утрата устройства
(`D3DERR_DEVICELOST`) — вопрос L6: в полноэкранном эксклюзивном режиме Windows теряет устройство при потере фокуса.

## Обработчики игры (факт из exe; проверка отображения, не его источник)

Главная оконная процедура обрабатывает сама: `WM_DESTROY`, `WM_SIZE` (возвращает 0), `WM_ACTIVATE` (ветви
`WA_INACTIVE` и `WA_ACTIVE`/`WA_CLICKACTIVE` при `HIWORD(wParam) == 0`), `WM_ERASEBKGND`, `WM_WINDOWPOSCHANGED`,
`WM_NCHITTEST` (счётчик `ShowCursor`), `WM_NCACTIVATE` (флаг при `wParam == 0`), `WM_SYSKEYUP`, `WM_SYSCOMMAND`
(`SC_SCREENSAVE`, `SC_MONITORPOWER` → 1), `WM_MOUSEMOVE`, `WM_IME_SETCONTEXT`. Остальное уходит в `DefWindowProcA`.

Следствия для `DefWindowProcA` и очереди (должны быть тотальны):
- `WM_CLOSE` → `DestroyWindow`; `WM_NCHITTEST` → `HTCLIENT` для точки в клиентской области; `WM_SYSCOMMAND(SC_CLOSE)` → `WM_CLOSE`.
- `WM_ACTIVATEAPP`, `WM_POWERBROADCAST` (ответ `TRUE`), `WM_DISPLAYCHANGE`, `WM_KILLFOCUS`, `WM_SETFOCUS` игра не
  обрабатывает: они проходят через `DefWindowProcA` без действия.
- `WM_QUIT` порождает только `PostQuitMessage` самой игры; платформа его не вставляет.

## Вопросы

- L6: теряется ли устройство D3D9 при потере фокуса (полноэкранный эксклюзивный режим) и как игра меняет размер
  back buffer при переходе док ↔ портатив (720p ↔ 1080p): `Reset` по `WM_DISPLAYCHANGE` игра сама не делает.
