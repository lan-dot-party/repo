#!/usr/bin/env bash
# sign-packages.sh [work/dl]: rpm -> rpmsign --addsign in place, arch -> detached .sig.
# apk packages are not re-signed (see docs/DESIGN.md).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/_keys.sh
source scripts/_keys.sh

DL="${1:-work/dl}"
setup_gpg
setup_apk_key

mapfile -t rpms < <(find "$DL/rpm" -type f -name '*.rpm' 2>/dev/null | sort)
mapfile -t pkgs < <(find "$DL/arch" -type f -name '*.pkg.tar.zst' 2>/dev/null | sort)

if [ "${#rpms[@]}" -gt 0 ]; then
  command -v rpmsign >/dev/null || { echo "rpmsign not found (apt install rpm); needed to sign ${#rpms[@]} rpm files" >&2; exit 1; }
  # Keep rpm's own __gpg_sign_cmd (its arguments differ between rpm versions) and
  # only swap the gpg binary for a wrapper that runs non-interactively.
  gpg_wrap="$_KEYS_TMP/gpg-rpm"
  {
    echo '#!/bin/sh'
    if [ -n "${REPO_GPG_PASSPHRASE:-}" ] && [ "$DRY_RUN_KEY" = 0 ]; then
      echo "exec gpg --batch --no-tty --pinentry-mode loopback --passphrase-file '$_KEYS_TMP/pass' --digest-algo sha256 \"\$@\""
    else
      echo 'exec gpg --batch --no-tty --digest-algo sha256 "$@"'
    fi
  } > "$gpg_wrap"
  chmod 700 "$gpg_wrap"
  for f in "${rpms[@]}"; do
    echo "rpmsign $f" >&2
    rpmsign --define "_gpg_name $FPR" --define "_gpg_path $GNUPGHOME" \
      --define "__gpg $gpg_wrap" --addsign "$f" >/dev/null
  done
fi

for f in "${pkgs[@]}"; do
  echo "sign $f" >&2
  rm -f "$f.sig"
  gpg "${GPG_OPTS[@]}" --digest-algo SHA256 -u "$FPR" --detach-sign --no-armor -o "$f.sig" "$f"
done
echo "sign-packages: ${#rpms[@]} rpm, ${#pkgs[@]} arch signed" >&2
