#!/bin/sh
# enum_run.sh [workdir] : writes enum_*.tsv/README here; builds and runs enum_probe in workdir (default /home/user/work/l6/enum)
set -e; H=$(cd "$(dirname "$0")" && pwd); N=$H/../../native; W=${1:-/home/user/work/l6/enum}; mkdir -p "$W"
python3 -B -I "$H/enum_spec.py" "$H"; gcc -std=gnu99 -O1 -Wall -Wextra -I"$N/include" -I"$N/front" -I"$N/../shaders/include" -o "$W/enum_probe" "$H/enum_probe.c" "$N/front/d3d9_state.c" -lm
"$W/enum_probe" > "$W/enum_probe.out"; python3 -B -I "$H/enum_cov.py" "$W" "$H"
