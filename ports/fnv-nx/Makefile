# fnv-nx build pipeline - one entry point for the whole build (stream S9-build, docs/BUILD.md).
#
#   make [VAR=value ...]                 all stages, incremental; writes build/report.md
#   make <stage> [VAR=value ...]         one stage and what it needs: fetch toolchain prepare census compile link
#                                        guest nine host package tests
#   make report | status | help          re-render the report / stage table / variables
#   make clean | clean-<stage>           remove build/ or one stage directory
#   make clean-work                      remove re-creatable work trees (clones, third-party build dirs)
#
# Typical (reusing an existing toolchain and checkouts):
#   make FNVNX_TC=/opt/fnvnx-a64 DEVKITPRO=/opt/fnvnx-dkp EXT=/path/to/ext PROGRAM_ID=01xxxxxxxxxxxxxx
#
# Variables are forwarded to tools/pipeline/build.py from the command line or the environment; build.py documents
# each one (make help). Everything under build/ is derived from the decomp and the player's game: never commit it.

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON ?= python3
PIPE := $(PYTHON) -I $(HERE)/tools/pipeline/build.py

VARS := BUILD_DIR EXT ALLOW_UNPINNED FNVNX_TC DEVKITPRO TC_DL JOBS GAME_CXX OPT CENSUS OVERRIDE EXTERN_TEXT \
        GUEST_LINK VORBIS NINE NINE_SRC PROGRAM_ID TESTS EMU_TIMEOUT FORCE CLEAN_WORK MIN_FREE_GB KEEP_HISTORY
ARGS := $(foreach v,$(VARS),$(if $(filter-out undefined default,$(origin $(v))),'$(v)=$($(v))'))
STAGES := fetch toolchain prepare census compile link guest nine host package tests

.DEFAULT_GOAL := all
.PHONY: all report status help clean clean-work $(STAGES) $(addprefix clean-,$(STAGES))

all:
	@$(PIPE) $(ARGS) all

# several stage goals (make link tests) are one pipeline run: the first goal runs them all, the others do nothing
GOALS := $(filter $(STAGES) report status help,$(MAKECMDGOALS))
$(STAGES) report status help:
	@$(if $(filter $@,$(firstword $(GOALS))),$(PIPE) $(ARGS) $(GOALS),:)

clean:
	@$(PIPE) $(ARGS) clean

clean-work:
	@$(PIPE) $(ARGS) clean-work

$(addprefix clean-,$(STAGES)):
	@$(PIPE) $(ARGS) clean $(patsubst clean-%,%,$@)
