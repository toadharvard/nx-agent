# l5-platform-w9-009: ASM-501 снят — границы функций из фактов L3a

Ветка claude/fnv-l5-platform-w9-fn. Единственный читатель: platform/manifest/funcfacts.py, пин facts.json (fnv-nx-build l3a-lift/facts @2eeea27, tree_sha256 проверяется, sha256 образа проверяется, ребейз на ImageBase).
Удалено: pemanifest.func_start и prologue-flow, regsites.Region-скан, regclass func_start, prefs_settings regex `cc 55 8b ec`.
Правила: адрес в нескольких entry — анализ в каждом + join (расхождение → dynamic); адрес вне run → `unresolved: inside no run`; образ без фактов → `unresolved: no function facts`; состояние top в heads (обратные переходы, начала run, другие entry внутри run).
Цифры (весь набор, 7 образов + Galaxy без фактов): entry exe 101590, atimgpud 660, binkw32 988, d3dx9_38 38853, d3dx9_31 18945, libvorbis 988, libvorbisfile 696.
join-конфликтов: dynsites 1 (exe 0x4de010 GetProcAddress: entry 4dd2c0 и внутренние 4dd78d/4dd796 дают разные строки → dynamic; было ADL_Main_Control_Destroy), regsites 0.
fontfaces: 6 API-сайтов, 0 вне entry, 0 в нескольких. prefs_settings: 74 настроек, совпадает с эталоном построчно, unresolved 0.
filesites: 398 строк (было 396): static 63 (было 75), dynamic 335 (было 321); 77 строк galaxy*.dll = `no function facts` (Galaxy не лифтится), 5 сайтов atimgpud вне run.
Для L3a: d3dx9_31.dll — 34 entry, чьи run не декодируются точно (напр. entry 0x4012d4-0x4013d1, 0x405aa0-0x405b7a); их сайты unresolved.
Диффы regsites: 2 строки, меняется только число «virtual call sites» в пояснении (1/4 → 0), строки те же.
Self-тесты check_regsites/check_filesites зелёные (синтетический PE объявляет свои entry через funcfacts.declare).
