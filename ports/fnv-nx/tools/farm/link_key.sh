#!/usr/bin/env bash
# link_key.sh <repo> <commit> - the link tools' key of a commit (T1 linkcheck): sha256 (16 hex) of the git object ids
# of the paths tools/pipeline/build.py link_tool_inputs hashes, plus build.py itself. linkcheck_job.sh names its
# verdicts by it, runner.sh's gate (RUNNER_GATE=linkcheck) looks them up by it: two commits with the same key link
# with the same tools. Reads only trees (a blobless clone is enough).
set -euo pipefail
r=${1:?repo}; c=${2:?commit}
for p in tools/link platform/crt/crt_symbols.tsv platform/win32/imports.tsv platform/win32/exports.cpp shim/include \
	tools/pipeline/check_rebind.py patches/units_renames.tsv tools/pipeline/build.py; do
	echo "$p $(git -C "$r" rev-parse -q --verify "$c:ports/fnv-nx/$p" 2>/dev/null || echo absent)"
done | sha256sum | cut -c1-16
