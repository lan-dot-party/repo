#!/usr/bin/env bash
# sign-metadata.sh [public]: sign deb Release, rpm repomd.xml, pacman dbs and apk APKINDEX.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/_keys.sh
source scripts/_keys.sh

PUB="${1:-public}"
setup_gpg
setup_apk_key
GPG=(gpg "${GPG_OPTS[@]}" --digest-algo SHA256 -u "$FPR")

n=0
# deb
while IFS= read -r rel; do
  d="$(dirname "$rel")"
  rm -f "$d/InRelease" "$d/Release.gpg"
  "${GPG[@]}" --clearsign -o "$d/InRelease" "$rel"
  "${GPG[@]}" --detach-sign --armor -o "$d/Release.gpg" "$rel"
  n=$((n + 1))
done < <(find "$PUB/deb/dists" -type f -name Release 2>/dev/null | sort)

# rpm
while IFS= read -r f; do
  rm -f "$f.asc"
  "${GPG[@]}" --detach-sign --armor -o "$f.asc" "$f"
  n=$((n + 1))
done < <(find "$PUB/rpm" -type f -name repomd.xml 2>/dev/null | sort)

# arch (db and files, tarball and plain copy)
while IFS= read -r f; do
  rm -f "$f.sig"
  "${GPG[@]}" --detach-sign --no-armor -o "$f.sig" "$f"
  n=$((n + 1))
done < <(find "$PUB/arch" -type f \( -name '*.db' -o -name '*.db.tar.gz' -o -name '*.files' -o -name '*.files.tar.gz' \) 2>/dev/null | sort)

# apk: sign the gzip stream, prepend the signature as its own gzip(tar) stream
keyname="$(basename "$KEYS/lan-party.rsa.pub")"
tmp="$(mktemp -d)"
while IFS= read -r f; do
  openssl dgst -sha1 -sign "$APK_KEY" -out "$tmp/sig" "$f"
  python3 -I scripts/common.py apk-prepend-sig "$tmp/sig" "$f" "$keyname" "$tmp/signed"
  mv "$tmp/signed" "$f"
  n=$((n + 1))
done < <(find "$PUB/alpine" -type f -name APKINDEX.tar.gz 2>/dev/null | sort)
rm -rf "$tmp"
echo "sign-metadata: $n files signed" >&2
