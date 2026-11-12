# l5-platform-w9-010: ворота ASM-501 и перегенерация выходов

fnv-nx-build l5-platform/manifest c30725e: regsites, filesites, fileset, fontfaces, prefs_settings, prefs_required перегенерированы (nx-agent 000d336a, facts 2eeea27); dynsites — новый файл dynsites_funcfacts.tsv (committed dynsites.tsv теперь выход w15 с call-VA и закрытыми строками, его не трогаю).
Диффы к 880555c (строк изменено, < и >): regsites 2/2, filesites 154/156 (396→398), fileset 214 по первым 6 колонкам, fontfaces 0, prefs_settings 0, dynsites(pass vs старый pass) 16/20.
fileset_classes воспроизводит committed fileset.tsv на committed filesites (shipped = manifest/data_listing.txt, platmods = столбец dll imports.tsv): 396 строк, затем на новых 398: device 4 (было 5), i 1, ii 4, iv 7, module 24 (30), sysmod 22 (27), unresolved 336 (322).
Ворота: platform/manifest/check_funcfacts.py (AST: P1 литерал прологона/паддинга вне списка байтов, P2 имена func_start/fend, P3 capstone.Cs вне funcfacts), самотест на синтетических источниках, на b2be0ff4 находит все три класса, на новом коде чисто (35 файлов). Подключён в tests/s4 regsites-check.
