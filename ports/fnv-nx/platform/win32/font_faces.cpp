/* font_faces.cpp - w32_face_name: the name of a declared face (font_faces.h). Kept in its own unit so that every platform request of a
 * face stays a call that the census finds in the compiled objects. */
#include "font_faces.h"

extern "C" const char *w32_face_name(int id)
{
    static const char *const names[] = {
#define W32_FACE_NAME(id, name, where) name,
        W32_FACES(W32_FACE_NAME)
#undef W32_FACE_NAME
    };
    unsigned i = (unsigned)id - (unsigned)W32_FACE_BASE;
    return i < sizeof names / sizeof names[0] ? names[i] : "";
}
