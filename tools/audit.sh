#!/bin/sh
# Pre-publish audit: personal data, secrets, binaries. Exit 1 on findings.
set -u
cd "$(dirname "$0")/.."
fail=0
files=$(git ls-files --cached --others --exclude-standard)

hits=$(printf '%s\n' "$files" | xargs grep -nIiE '/Users/|/home/[a-z]|privaterelay|@yandex|BISKEY|eticket|XA[WJKL][0-9]{10}|serial_?number *[:=]|oauth_token|gh[pousr]_[A-Za-z0-9]{20,}' 2>/dev/null | grep -v '^tools/audit.sh:')
keyhits=$(printf '%s\n' "$files" | grep -v '/tests/' | xargs grep -nIiE '(prod|title|dev)\.keys' 2>/dev/null | grep -v '^tools/audit.sh:' | grep -viE 'never|refus|denied|off limits|block')
hits=$(printf '%s\n%s' "$hits" "$keyhits" | sed '/^$/d')
if [ -n "$hits" ]; then echo "personal/secret patterns:"; echo "$hits"; fail=1; fi

for f in $files; do
  [ -f "$f" ] || continue
  case "$(file -b "$f")" in
    *ELF*|*Mach-O*|*PE32*|*"Zip archive"*) echo "binary: $f"; fail=1 ;;
  esac
  size=$(wc -c < "$f")
  [ "$size" -gt 524288 ] && { echo "large file: $f ($size bytes)"; fail=1; }
  case "$f" in *.nsp|*.nso|*.nca|*.bin|*.keys) echo "forbidden type: $f"; fail=1 ;; esac
done

if command -v gitleaks >/dev/null; then gitleaks detect --no-git --source . --redact || fail=1; fi
[ $fail -eq 0 ] && echo "audit clean"
exit $fail
