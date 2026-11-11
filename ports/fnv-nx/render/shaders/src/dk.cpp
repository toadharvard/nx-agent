// dk.cpp - GLSL -> DKSH through uam's library API (uam_api.h, added by uam.patch).
#include <stdio.h>
#include <string.h>
#include "uam_api.h"
#include "dk_api.h"

extern "C" int fnvnx_dk_compile(fnvnx_shader_stage stage, const char* glsl, fnvnx_dk_out* out, char* err, size_t errlen)
{
	memset(out, 0, sizeof(*out));
	// uam_api stage numbering = uam's pipeline_stage: 0 vertex, 4 fragment
	int s = stage == FNVNX_STAGE_VERTEX ? 0 : 4;
	uam_result r;
	if (!uam_compile_dksh(s, glsl, &r))
	{
		snprintf(err, errlen, "uam: GLSL compile failed (diagnostics on stderr)");
		return -1;
	}
	out->dksh = r.dksh; // ownership moves to caller (malloc'd)
	out->size = r.dksh_size;
	out->instr = r.num_instructions;
	out->gprs = r.num_gprs;
	out->code = r.code_size;
	out->cb1 = r.constbuf1_size;
	return 0;
}
