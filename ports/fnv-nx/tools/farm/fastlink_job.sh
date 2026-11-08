#!/usr/bin/env bash
# fastlink_job.sh <commit> <report.md> - runner job `fastlink`: linkcheck_job.sh in FAST mode (see there): the head's
# link on a published tree with its prepare inputs and recon set, at a fixed path with warm caches, then the guest,
# link/<c12> and link-head published at once, then S5's stub boot. Target: under 15 min per head. Its sidecar is
# fnvnx-linkcheck/1 as well (the runner's gate RUNNER_GATE=linkcheck reads *-fastlink.json too).
LINKCHECK_FAST=1 exec bash "$(dirname "$0")/linkcheck_job.sh" "$@"
