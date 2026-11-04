# recon.sh - fetch the reconstructed bodies (D20) for a prepare run (sourced by prepare_job.sh and full_job.sh).
#
#   fetch_recon <artifacts clone> <dir>   -> exports FNVNX_RECON=<dir> (S1-refs' rule REC1 reads it) and RECON_ID
#
# Every branch recon/* of the private artifacts repository (toadharvard/fnv-nx-build) goes to <dir>/<branch name
# after recon/>, from its tip (git archive, no checkout of the clone). RECON_ID is a hash of the branch names and
# tips, empty when there is none: prepare's reuse key, so a new or changed recon prepares again (the tree hash
# changes; that is expected). No recon branch, or a failed listing, is not an error: FNVNX_RECON is unset and REC1
# does nothing. The recon bodies are exe-derived: they stay in <dir> and the private repository, never in nx-agent.
fetch_recon() {
	local art=$1 dir=$2 refs b sha n=0
	unset FNVNX_RECON; RECON_ID=""; RECON_NOTE="no recon/* branch"
	rm -rf "$dir"
	refs=$(git -C "$art" ls-remote origin 'refs/heads/recon/*' 2>/dev/null | sort -k2) || refs=""
	[ -n "$refs" ] || { export RECON_ID RECON_NOTE; return 0; }
	mkdir -p "$dir"
	while read -r sha b; do
		b=${b#refs/heads/}
		if git -C "$art" fetch -q origin "+refs/heads/$b:refs/remotes/origin/$b" 2>/dev/null \
				&& mkdir -p "$dir/${b#recon/}" \
				&& git -C "$art" archive "$sha" | tar -x -C "$dir/${b#recon/}"; then
			n=$((n + 1))
		else
			echo "fetch_recon: $b could not be fetched, left out" >&2; rm -rf "${dir:?}/${b#recon/}"
		fi
	done <<<"$refs"
	if [ "$n" -gt 0 ]; then
		RECON_ID=$(find "$dir" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|  $dir/|  |" | sha256sum | cut -c1-16)
		export FNVNX_RECON=$dir
		RECON_NOTE="$n recon branch(es), $(find "$dir" -name recon.json | wc -l) recon.json, id $RECON_ID"
	else
		RECON_NOTE="recon/* listed but none fetched"; rm -rf "$dir"
	fi
	export RECON_ID RECON_NOTE
}
