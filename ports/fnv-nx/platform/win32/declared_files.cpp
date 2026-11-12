/* declared_files.cpp - the declared machine's installed files that a program other than the exe writes (owner rule: the machine is
 * installed with the launcher run once for the declared adapter; FalloutPrefs.ini holds exactly the key the exe's own code requires
 * before its main path, here the launcher gate's sD3DDevice = the adapter Description our d3d9 answers, and nothing else, so every other
 * key is absent and the exe uses its built-in defaults). boot.cpp calls fnvnx_declared_files_first_run() before WinMain.
 *
 * The rows are generated (declared_files.gen.inc by platform/manifest/gen_prefs_ini.py) from the exe's gate facts and the render front
 * end's adapter table; nothing is written by hand and no file is read at run time. A file is made only when it does not exist, so what
 * the exe or the player changed is never overwritten. The directory is relative to SHGetFolderPathA(CSIDL_PERSONAL), the exe's own
 * profile directory. */
#include "w32.h"

#include <string.h>
#include <string>

extern "C" void fnvnx_declared_files_first_run(void);

namespace {
struct DeclFile { const char *dir, *name, *text; unsigned size; };

const DeclFile kFiles[] = {
#include "declared_files.gen.inc"
    { NULL, NULL, NULL, 0 }
};

bool make_dirs(std::string base, const char *rel)
{
    for (const char *p = rel; *p;) {
        const char *e = strchr(p, '\\');
        if (!e) return false;
        base.push_back('\\');
        base.append(p, (size_t)(e - p));
        if (!CreateDirectoryA(base.c_str(), NULL) && GetLastError() != ERROR_ALREADY_EXISTS) return false;
        p = e + 1;
    }
    return true;
}
} // namespace

extern "C" void fnvnx_declared_files_first_run(void)
{
    char personal[MAX_PATH];
    if (SHGetFolderPathA(NULL, 5 /* CSIDL_PERSONAL */, NULL, 0, personal) != S_OK) {
        W32Log().s("declared files: SHGetFolderPathA(CSIDL_PERSONAL) failed\n").emit();
        return;
    }
    for (const DeclFile *f = kFiles; f->name; f++) {
        std::string path = std::string(personal) + "\\" + f->dir + f->name;
        if (GetFileAttributesA(path.c_str()) != INVALID_FILE_ATTRIBUTES) {
            W32Log().s("declared files: ").s(f->name).s(" exists, nothing applied\n").emit();
            continue;
        }
        HANDLE h = make_dirs(personal, f->dir) ? CreateFileA(path.c_str(), GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL)
                                              : INVALID_HANDLE_VALUE;
        DWORD put = 0;
        bool ok = h != INVALID_HANDLE_VALUE && WriteFile(h, f->text, f->size, &put, NULL) && put == f->size;
        if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
        W32Log().s("declared files: ").s(f->name).s(ok ? " made\n" : " FAILED with ").emit();
        if (!ok) W32Log().u((unsigned)GetLastError()).s("\n").emit();
    }
}
