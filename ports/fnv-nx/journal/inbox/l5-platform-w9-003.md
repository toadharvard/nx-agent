# l5-platform-w9-003: хвост vorbis

Удалено: toolchain/vorbis/ (build.sh); аргумент [vorbis prefix]/VORBIS/VFLAGS/-lvorbisfile в toolchain/game/build.sh;
provide_vorbis, конфиг VORBIS, артефакт/метрика vorbis в tools/pipeline/{build,report}.py; VORBIS в Makefile, tools/farm/*_job.sh,
full_report.py, tools/threads/variant.sh, render/nine/game/build.sh, tests/s5/farm_boot.sh, tests/t1/*, tests/s9/test_toolchain.py (6 OK);
доки TOOLCHAIN/BUILD/AUDIO_INPUT.
Остались упоминания vorbis (grep -rIl -i vorbis, без journal): ENV_TRUTH.md, win32/imports.tsv, win32/exports.cpp (комментарий M1),
win32/platstat.cpp (класс decode), tools/pipeline/undef.py (класс по имени ov_*, чужой слой), tests/s18/test_scenario.py и
tools/scenario/scenario.py (разбор лога), tools/perf/PLAT.md, tools/farm/sweep_job.sh (DLL-файлы игры), docs/RUNTIME.md, docs/AUDIO_INPUT.md (описание импортов).
Побочно: TCID-хэш тулчейна в farm-скриптах больше не включает vorbis/build.sh, ключи тулчейна поменяются один раз.
