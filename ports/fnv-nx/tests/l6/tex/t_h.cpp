// part h: format table completeness. Every format the env table (python oracle from the tsv) allows for a resource class / bind must have a memory layout (res_fmt.cpp) and a native format
// (nx_d3d9_format) with the render / depth capability the bind needs; zero-size classes (INST, RESZ, ATOC, NVDB, unknown) must have no layout. A hole is a TEST ERROR (not a run-time failure).
#include "t_common.h"
void t_h() {
    t_part('h'); FILE* f = t_open("fmts.txt"); char line[256];       // F rtype usage fmt | Z fmt
    while (f && fgets(line, sizeof line, f)) {
        unsigned rt, us, fmt; NxTexFmt tf;
        if (line[0] == 'Z') { sscanf(line, "Z %u", &fmt); CHECK(!nx_texfmt(fmt, &tf)); continue; }
        if (sscanf(line, "F %u %u %u", &rt, &us, &fmt) != 3) { CHECK(false); continue; }
        bool lay = nx_texfmt(fmt, &tf); NxFormatInfo ni = nx_d3d9_format(fmt);
        if (!CHECK(lay)) printf("  no layout: fmt %u\n", fmt);
        if (!CHECK(ni.format != NxFmt_None)) printf("  no NxFormat: resource %u usage %u fmt %#x\n", rt, us, fmt);
        else if (us == 1) CHECK(ni.flags & NXFMT_RENDER); else if (us == 2) CHECK(ni.flags & NXFMT_DEPTH);
    }
    if (f) fclose(f);
}
