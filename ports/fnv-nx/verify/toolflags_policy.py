"""toolflags policy table (data only): key -> (kind, requirement, citation). The reviewable statement of assumption A-env. (ASM-038)
GCC 13.3 / binutils 2.42 rows were checked against the installed --help."""
RB = "reproducible-builds.org"
GCCENV = "GCC manual, Environment Variables"
FORBID_ENV_GCC = "GCC_EXEC_PREFIX COMPILER_PATH LIBRARY_PATH CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH DEPENDENCIES_OUTPUT SUNPRO_DEPENDENCIES"
KINDS_TXT = "gcc/as/ld/ar/objcopy/python"
KINDS = ("gcc", "as", "ld", "ar", "objcopy", "python")
GNU = ("gcc", "as", "ld", "ar", "objcopy")
FIXED = (("TZ", "UTC"), ("LC_ALL", "C"), ("PYTHONHASHSEED", "0"))
FORBID_ENV = ("LANG", "LANGUAGE", "PYTHONPATH", "HOME", "USER", "LOGNAME", "HOSTNAME", "PWD", "DISPLAY", "GCC_EXEC_PREFIX",
              "COMPILER_PATH", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "DEPENDENCIES_OUTPUT",
              "SUNPRO_DEPENDENCIES", "MAKEFLAGS", "MFLAGS", "MAKELEVEL")
PYENV_OK = ("PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE")


POLICY = {
    "env.fixed": ("all", "env TZ=UTC LC_ALL=C PYTHONHASHSEED=0 SOURCE_DATE_EPOCH=<integer> COLUMNS=<integer> present",
                  RB + ", Standard Environment Variables (SOURCE_DATE_EPOCH, TZ, LC_ALL); Python docs, PYTHONHASHSEED"),
    "env.forbidden": ("all", "env has no LANG LANGUAGE LC_*(except LC_ALL) PYTHON*(except PYTHONHASHSEED, "
                      "PYTHONDONTWRITEBYTECODE) HOME USER LOGNAME HOSTNAME PWD DISPLAY CCACHE_* DISTCC_* MAKEFLAGS MFLAGS "
                      "MAKELEVEL; GCC input variables " + FORBID_ENV_GCC,
                  RB + ", Standard Environment Variables / Locales; " + GCCENV + "; Python docs, Using Python, Environment"),
    "env.path": ("all", "PATH present, every component under a declared input", RB + ", Deterministic build systems (PATH)"),
    "env.tmpdir": ("all", "TMPDIR, if present, under a declared output", GCCENV + " (TMPDIR)"),
    "tool.kind": ("all", "document declares tool in " + KINDS_TXT + "; sh, make, other refused",
                  "child commands of sh/make are not visible in argv; a stage is one tool invocation"),
    "args.jobs": ("all", "no -j/-jN/--jobs in argv (objcopy -j is --only-section: exempt; python: only before the script); "
                  "'jobs' field absent or integer", "binutils objcopy --help (-j --only-section); a stage is one invocation"),
    "resp": ("gnu", "response files only under a declared input, contents checked like argv",
             "GCC manual, Overall Options (@file); binutils ld manual, Command-line Options (@file)"),
    "gcc.seed": ("gcc", "-frandom-seed=<non-empty>", "GCC manual, Developer Options (-frandom-seed); " + RB + ", Deterministic build systems"),
    "gcc.prefix": ("gcc", "-ffile-prefix-map=OLD=NEW", "GCC manual, Overall Options (-ffile-prefix-map); " + RB + ", Build path"),
    "gcc.date": ("gcc", "-Werror=date-time; -Wno-date-time and -Wno-error=date-time forbidden", "GCC manual, Warning Options (-Wdate-time)"),
    "gcc.forbid": ("gcc", "forbidden: -march=native -mtune=native -fprofile-generate* -fbranch-probabilities -save-temps*; "
                   "-fprofile-use/-fauto-profile only as =<path under input>",
                   "GCC manual, x86 Options (native: host CPU); Optimize Options (profile data is an input); "
                   "-save-temps writes files outside declared outputs"),
    "gcc.paths": ("gcc", "-fplugin=P and -specs=P with P under a declared input", "GCC manual, Overall Options (-specs), Optimize Options (-fplugin)"),
    "as.debug": ("as", "-g/--gen-debug/--gdwarf-N needs --debug-prefix-map; no other requirement",
                 "GNU as manual, Command-Line Options (--debug-prefix-map); " + RB + ", Build path"),
    "ld.buildid": ("ld", "--build-id[=sha1|md5|none|0xHEX] present; --build-id=uuid and unknown styles forbidden",
                   "GNU ld manual, Options (--build-id: uuid is random; bare = sha1)"),
    "ld.paths": ("ld", "-T/--script P under a declared input (-Map files are outputs)", "GNU ld manual, Options (-T)"),
    "ar.D": ("ar", "operation letters contain D and not U", "GNU ar manual, Modifiers (D deterministic mode, U real timestamps)"),
    "objcopy.D": ("objcopy", "-D/--enable-deterministic-archives present; -p/--preserve-dates, -U forbidden",
                  "GNU binutils objcopy manual (-D, -p, -U)"),
    "python.flags": ("python", "-s and (-B or env PYTHONDONTWRITEBYTECODE=1); -I and -E forbidden (they drop PYTHONHASHSEED, "
                     "measured: hash(\"a\") differs per run under -I); -c and -m forbidden",
                     "Python docs, Command line (-B, -E, -I, -s); PEP 552 (pyc embed source mtime)"),
    "python.script": ("python", "script path present, under a declared input", "Python docs, Command line (script)"),
}
