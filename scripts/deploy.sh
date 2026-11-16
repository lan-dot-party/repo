#!/usr/bin/env bash
# deploy.sh [public]: upload via lftp over FTPS (docs/DESIGN.md step 9).
# Order: (a) pool files, (b) metadata + landing page, (c) deletions.
# Env: FTP_HOST FTP_USER FTP_PASSWORD FTP_PATH
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PUB="${1:-public}"
if [ -e work/.dry-run-key ]; then
  echo "REFUSING to deploy: work/.dry-run-key exists (throwaway signing key was used)." >&2
  exit 1
fi
for v in FTP_HOST FTP_USER FTP_PASSWORD FTP_PATH; do
  [ -n "${!v:-}" ] || { echo "$v is not set" >&2; exit 1; }
done
command -v lftp >/dev/null || { echo "lftp not installed" >&2; exit 1; }
[ -f "$PUB/deploy-plan.json" ] || { echo "$PUB/deploy-plan.json missing" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Lists: upload (pool), meta (everything else in public/, entry points last), delete.
python3 -I - "$PUB" "$tmp" <<'PY'
import json, os, re, sys
pub, tmp = sys.argv[1:3]
plan = json.load(open(os.path.join(pub, "deploy-plan.json")))
safe = re.compile(r"^[A-Za-z0-9._+~/@-]+$")
files = []
for root, _dirs, names in os.walk(pub):
    for n in names:
        files.append(os.path.relpath(os.path.join(root, n), pub))
upload = [p for p in plan["upload"] if p in set(files)]
skip = set(plan["upload"]) | {"deploy-plan.json"}
entry = re.compile(r"(^|/)(InRelease|Release|Release\.gpg|repomd\.xml(\.asc)?|APKINDEX\.tar\.gz|"
                   r"[^/]+\.(db|files)(\.tar\.gz)?(\.sig)?|index\.html)$")
meta = sorted(p for p in files if p not in skip)
meta = [p for p in meta if not entry.search(p)] + [p for p in meta if entry.search(p)]
for name, lst in (("upload", upload), ("meta", meta), ("delete", plan["delete"])):
    for p in lst:
        if not safe.match(p) or ".." in p.split("/"):
            sys.exit(f"unsafe path: {p!r}")
    open(os.path.join(tmp, name + ".txt"), "w").write("".join(p + "\n" for p in lst))
PY

{
  echo "set cmd:fail-exit yes"
  echo "set ftp:ssl-force true"
  echo "set ftp:ssl-protect-data true"
  echo "set ssl:verify-certificate yes"
  echo "set net:max-retries 5"
  echo "set net:timeout 30"
  echo "open --env-password -u \"$FTP_USER\" \"ftp://$FTP_HOST\""
  echo "cd \"$FTP_PATH\""
  for l in upload meta; do
    # create directories first
    while IFS= read -r d; do echo "mkdir -p -f \"$d\""; done < <(sed -n 's|/[^/]*$||p' "$tmp/$l.txt" | sort -u)
    while IFS= read -r f; do echo "put -O \"$(dirname "$f")\" \"$PUB/$f\""; done <"$tmp/$l.txt"
  done
  while IFS= read -r f; do echo "rm -f \"$f\""; done <"$tmp/delete.txt"
  echo "bye"
} >"$tmp/script.lftp"

echo "deploy: $(wc -l <"$tmp/upload.txt") pool files, $(wc -l <"$tmp/meta.txt") metadata files, $(wc -l <"$tmp/delete.txt") deletions" >&2
export LFTP_PASSWORD="$FTP_PASSWORD"
lftp -f "$tmp/script.lftp"
