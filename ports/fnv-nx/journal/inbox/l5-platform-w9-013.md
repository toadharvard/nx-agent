# l5-platform-w9-013: слияние platform/manifest, таблица по файлам (ours = база e6aeacd50 = w10-trunk 3bfa841 + w12 шрифты + hostabi; theirs = старая линия 5fd10223)

Ancestor A0 = манифест w10-trunk 3bfa841 (база отличается от него в 8 файлах: шрифты w12 f2adc9ad3, hostabi ce96e83e2). Старая линия ушла от A0 в 37 файлах + 11 новых. Конфликтов 3-way (git merge-file) нет: ни один файл не менялся с обеих сторон. 5 файлов add/add (шрифты, нет A0) решены вручную.

| файл | A0/ours/theirs | решение |
|---|---|---|
| DOUBLES.md | 111 | theirs (ours unchanged since A0) |
| check_doubles.py | 111 | theirs (ours unchanged since A0) |
| check_dupdecl.py | 111 | ours (theirs unchanged since A0) |
| check_filesites.py | 111 | theirs (ours unchanged since A0) |
| check_funcfacts.py | 001 | added from old line |
| check_gen_fontmachine.py | 011 | ours (add/add; base adds the fontfamilies check) |
| check_hostapi.py | 111 | ours (theirs unchanged since A0) |
| check_regsites.py | 111 | theirs (ours unchanged since A0) |
| check_test_threads.py | 001 | added from old line |
| doubles_needs.tsv | 111 | theirs (ours unchanged since A0) |
| doubles_needs/doubles_needs.py | 111 | theirs (ours unchanged since A0) |
| doubles_needs/hooks.cpp | 001 | added from old line |
| doubles_needs_m32.tsv | 001 | added from old line |
| doubles_needs_s7.tsv | 001 | added from old line |
| doubles_needs_s7_vclock.tsv | 001 | added from old line |
| escape_fixture/expected.tsv | 110 | kept (L5: files only in base; old line deleted it, base unchanged since A0) |
| escape_fixture/fx_bridge.tsv | 110 | kept (L5: files only in base; old line deleted it, base unchanged since A0) |
| escape_fixture/fx_contract.tsv | 110 | kept (L5: files only in base; old line deleted it, base unchanged since A0) |
| escape_fixture/fx_contract_bad.tsv | 110 | kept (L5: files only in base; old line deleted it, base unchanged since A0) |
| facts.json | 001 | added from old line |
| fileset_classes.py | 111 | theirs (ours unchanged since A0) |
| filesites.py | 111 | theirs (ours unchanged since A0) |
| fontfaces.py | 011 | theirs (add/add; old line is the evolution: dllkind closure, facts; base copy = w12 original) |
| fontfamilies.py | 010 | kept (base only: w12 font work) |
| fontgate.py | 011 | ours + old line ASM-id text (4fb3a0e36) |
| fonts_mirror.json | 001 | added from old line |
| funcfacts.py | 001 | added from old line |
| gen_env_const.py | 111 | theirs (ours unchanged since A0) |
| gen_fontmachine.py | 011 | ours (fontfamilies, no fc-scan) + old line ASM-id text (4fb3a0e36) |
| gen_prefs_ini.py | 111 | theirs (ours unchanged since A0) |
| gen_registry_seed.py | 111 | theirs (ours unchanged since A0) |
| import_escape.py | 111 | theirs (ours unchanged since A0) |
| pemanifest.py | 111 | theirs (ours unchanged since A0) |
| platform_faces.py | 011 | ours (AArch64 scan, W32_FACE_BASE) + old line ASM-id text (4fb3a0e36) |
| prefs_settings.py | 111 | theirs (ours unchanged since A0) |
| probe_dynsites.cpp | 001 | added from old line |
| regclass.py | 111 | theirs (ours unchanged since A0) |
| regsites.py | 111 | theirs (ours unchanged since A0) |
| rtti.py | 111 | theirs (ours unchanged since A0) |
| test_import_escape.sh | 001 | added from old line |

18 файлов без различий между сторонами (ours = theirs = A0) не перечислены.
Дополнительно: resolve_dynsites.py не взят (красный по G1, w15 переносит на funcfacts); fontfamilies.py: `OWNER 3` -> ASM-003 (gate assumptions).
Старая линия удалила escape_fixture/* (4 файла), база их не меняла: оставлены по указанию L5 (ссылок на них нет у старой линии; решение L5).
