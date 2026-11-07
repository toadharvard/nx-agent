/* menu_standins.c - boot stand-ins on the stretch from renderer init to the main menu (stream S5-menu), for
 * functions whose native behaviour cannot be right yet on the Switch or in the emulator. tests/s5/native_render_inject.sh
 * compiles this file into nxrender_inject.o; tests/s5/native_render.py then replaces the guest's function of each
 * name with the definition here. The comment of each says what the exe does, why it cannot run as is, who fixes
 * it, and when the stand-in goes. Our own code; it names exe addresses only. */

/* 0x004da570, the launcher gate (WinMain, after the INI loads; renderer.cpp in the decomp): the exe compares the
 * [Display] sD3DDevice setting (0x011c72c0) with adapter 0's D3DADAPTER_IDENTIFIER9 Description (NiDX9SystemDesc
 * 0x00e69a60, adapter +0x204). When they differ ("the video card changed") it returns true, and WinMain
 * ShellExecutes the PC launcher (FalloutNVLauncher.exe, which rewrites FalloutPrefs.ini) and returns 0. On a match it
 * stores the quoted description back and returns false.
 * On the Switch there is no launcher. Two things are missing: the first-run profile does not write
 * sD3DDevice="<the adapter's Description>" as the launcher does (S5-data, platform/win32/profile.cpp), and the INI
 * load itself is stubbed (0x005e0200, a call past the end of a short native vtable: S2c's vtable extension), so the
 * setting keeps its default and never matches. The stand-in answers as on a match: the device is the configured one.
 * Remove when the INI load works and the profile writes sD3DDevice. */
_Bool fnv_standin_004da570(void) __asm__("_ZN12fnv_004da57012FUN_004da570Ev");
_Bool fnv_standin_004da570(void) { return 0; }
