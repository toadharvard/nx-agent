/* sdlayout.h - where fnv-nx lives on the SD card (stream NX1, docs/NRO.md). One place for every path the launcher,
 * the host and the guest use; tests/nx1/check_fs.py reads this file and checks the package against it.
 *
 *   sdmc:/switch/fnv/fnv.nro          the launcher, listed by hbmenu (toolchain/nro/launcher.c)
 *   sdmc:/switch/fnv/fnv-exefs.nsp    the game process: LP64 host "main" + main.npdm (32BitNoReserved), D8
 *   sdmc:/switch/fnv/guest.nso        the ILP32 game module, loaded by the host
 *   sdmc:/switch/fnv/FalloutNV.exe    the player's exe (data image, S3)       = C:\FalloutNV\FalloutNV.exe
 *   sdmc:/switch/fnv/Data/            the player's Data folder                = C:\FalloutNV\Data
 *   sdmc:/switch/fnv/user/            profile: Documents/My Games/FalloutNV (INI, Saves), AppData/Local/FalloutNV
 *   sdmc:/switch/fnv/fnvnx.ini        platform options (host.c)
 *   sdmc:/switch/fnv/host.log, crash.log, launcher.log
 *
 * The launcher copies fnv-exefs.nsp to sdmc:/atmosphere/contents/<program id>/exefs.nsp of the title it was
 * started from (title takeover), writes FNVNX_SD_MARKER and restarts that title; the host removes both once it runs,
 * so nothing stays installed. */
#ifndef FNVNX_SDLAYOUT_H
#define FNVNX_SDLAYOUT_H

#define FNVNX_SD_DIR       "sdmc:/switch/fnv"
#define FNVNX_SD_NRO       FNVNX_SD_DIR "/fnv.nro"
#define FNVNX_SD_EXEFS     FNVNX_SD_DIR "/fnv-exefs.nsp"
#define FNVNX_SD_GUEST     FNVNX_SD_DIR "/guest.nso"
#define FNVNX_SD_EXE       FNVNX_SD_DIR "/FalloutNV.exe"
#define FNVNX_SD_DATA      FNVNX_SD_DIR "/Data"
#define FNVNX_SD_USER      FNVNX_SD_DIR "/user"
#define FNVNX_SD_CONFIG    FNVNX_SD_DIR "/fnvnx.ini"
#define FNVNX_SD_HOST_LOG  FNVNX_SD_DIR "/host.log"
#define FNVNX_SD_CRASH_LOG FNVNX_SD_DIR "/crash.log"
#define FNVNX_SD_LAUNCH_LOG FNVNX_SD_DIR "/launcher.log"
/* "<program id hex16>\n": the override the launcher installed, removed by the host (or by the launcher's Y). */
#define FNVNX_SD_MARKER    FNVNX_SD_DIR "/override.txt"
/* the game's own shaders, precompiled at install time by toolchain/nro/shaders_nx.py (the render machine's
 * nxsp_pack.py over sdp2dksh) from the player's Data/Shaders: translated.nxsp, NXSP v1 by bytecode hash
 * (RENDER_NATIVE.md 4.5, check 9). Made from the player's files, so on the card next to the game, never in the
 * farm-built fnv.nro's RomFS; the guest reads it into nx::TranslatedStore. */
#define FNVNX_SD_TRANSLATED FNVNX_SD_DIR "/translated.nxsp"
/* The same paths without the device prefix, as fsFs* calls take them (crash handler: no newlib there). */
#define FNVNX_FS_CRASH_LOG "/switch/fnv/crash.log"
#define FNVNX_FS_MARKER    "/switch/fnv/override.txt"
#define FNVNX_ATMOSPHERE_OVERRIDE_FMT "sdmc:/atmosphere/contents/%016lx/exefs.nsp"
#define FNVNX_FS_OVERRIDE_FMT         "/atmosphere/contents/%016lx/exefs.nsp"

/* Names under FNVNX_SD_DIR that belong to the port; everything else there is the player's install (C:\FalloutNV).
 * tests/s5/datapath.py (S5-data) keeps these on the card, tests/nx1/check_fs.py checks the list covers every
 * FNVNX_SD_* above. drive_* = drive_<x> for any other Windows drive (path.cpp). */
#define FNVNX_SD_PORT_NAMES "fnv.nro fnv-exefs.nsp guest.nso fnvnx.ini user drive_* host.log crash.log launcher.log " \
                            "override.txt README.txt nxgpu.log translated.nxsp translated.json"

/* Memory the game process needs from the application pool (docs/NRO.md, check 2): the 32BitNoReserved heap region
 * is 2 GiB, so the host takes at most that; below FNVNX_MIN_APP_MEMORY the launcher and the host refuse to start. */
#define FNVNX_MIN_APP_MEMORY (2048ull << 20)

#endif
