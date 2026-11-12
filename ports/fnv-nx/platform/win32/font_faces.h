/* font_faces.h - the faces that platform code requests, the one declared table (L5 declared machine).
 * The declared font set is the faces that the exe, its modules and the platform's own call sites can request, each mapped by Wine 9.0's
 * font mapper to the files Wine ships (platform/manifest/fontfaces.py, check_fontfaces.py). A platform call site names a face only by
 * w32_face_name(FACE_x) with a constant id: the census reads those calls from the compiled objects (relocations of w32_face_name and
 * the immediate argument), so a face that is not in this table cannot be requested by the API of this layer.
 *   FACE(id, name, where the platform asks for it)                                                                                      */
#ifndef FNVNX_PLATFORM_WIN32_FONT_FACES_H
#define FNVNX_PLATFORM_WIN32_FONT_FACES_H

#define W32_FACES(FACE) \
    FACE(TAHOMA, "Tahoma", "display.cpp: CaptionFont/MenuFont of the window metrics (Wine 9.0 win32u sysparams default NONCLIENTMETRICS)") \
    FACE(ARIAL, "Arial", "window.cpp: message box icon glyph") \
    FACE(COURIER, "Courier", "gdifont.cpp stock_lf: ANSI_FIXED_FONT, SYSTEM_FIXED_FONT (win32u gdiobj.c)") \
    FACE(MS_SANS_SERIF, "MS Sans Serif", "gdifont.cpp stock_lf: ANSI_VAR_FONT (win32u gdiobj.c)") \
    FACE(SYSTEM, "System", "gdifont.cpp stock_lf: SYSTEM_FONT, DEVICE_DEFAULT_FONT (win32u gdiobj.c)") \
    FACE(MS_SHELL_DLG, "MS Shell Dlg", "gdifont.cpp stock_lf: DEFAULT_GUI_FONT (win32u gdiobj.c)") \
    FACE(COURIER_NEW, "Courier New", "gdifont.cpp enum_fallback: fixed pitch (win32u font.c default_fonts)") \
    FACE(LIBERATION_MONO, "Liberation Mono", "gdifont.cpp enum_fallback: fixed pitch (fontconfig-derived name)") \
    FACE(BITSTREAM_VERA_SANS_MONO, "Bitstream Vera Sans Mono", "gdifont.cpp enum_fallback: fixed pitch") \
    FACE(TIMES_NEW_ROMAN, "Times New Roman", "gdifont.cpp enum_fallback: serif") \
    FACE(LIBERATION_SERIF, "Liberation Serif", "gdifont.cpp enum_fallback: serif") \
    FACE(BITSTREAM_VERA_SERIF, "Bitstream Vera Serif", "gdifont.cpp enum_fallback: serif") \
    FACE(LIBERATION_SANS, "Liberation Sans", "gdifont.cpp enum_fallback: sans serif") \
    FACE(BITSTREAM_VERA_SANS, "Bitstream Vera Sans", "gdifont.cpp enum_fallback: sans serif")

/* An id is W32_FACE_BASE + the row's index: a value that no other constant of the platform equals, so the compiler cannot reuse a register that
 * holds the same number for another purpose and hide the id from the census (the census reads the id from the argument register). */
enum { W32_FACE_BASE = 0x46414300 };
enum w32_face_id {
    FACE__BEFORE_FIRST = W32_FACE_BASE - 1,
#define W32_FACE_ENUM(id, name, where) FACE_##id,
    W32_FACES(W32_FACE_ENUM)
#undef W32_FACE_ENUM
    FACE__END
};

#ifdef __cplusplus
extern "C"
#endif
const char *w32_face_name(int id);   /* defined in font_faces.cpp, never inlined: the call is the census' site */
#endif
