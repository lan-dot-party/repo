#!/usr/bin/env bash
# verify.sh <base-url> <docker-image>
# Runs the repo setup snippet (site/snippets/*.sh, same as the landing page) in a container
# and installs every package index/ lists for that distro.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BASE="${1:?usage: verify.sh <base-url> <docker-image>}"
IMAGE="${2:?usage: verify.sh <base-url> <docker-image>}"
BASE="${BASE%/}"

case "$IMAGE" in
  debian:12)     fam=deb  snippet=deb-sources suite=bookworm target=bookworm ;;
  debian:13)     fam=deb  snippet=deb-sources suite=trixie   target=trixie ;;
  ubuntu:24.04)  fam=deb  snippet=deb-list    suite=noble    target=noble ;;
  ubuntu:26.04)  fam=deb  snippet=deb-sources suite=resolute target=resolute ;;
  fedora:44)     fam=rpm  snippet=rpm-fedora  suite=""       target=fedora/44 ;;
  rockylinux:9)  fam=rpm  snippet=rpm-el      suite=""       target=el/9 ;;
  archlinux|archlinux:*) fam=arch snippet=arch suite=""      target=x86_64 ;;
  alpine:3.21)   fam=apk  snippet=alpine      suite=""       target=x86_64 ;;
  *) echo "unsupported image: $IMAGE" >&2; exit 2 ;;
esac

fmt="$fam"
pkgs="$(python3 -I scripts/common.py packages-for "$fmt" "$target")"
if [ -z "$pkgs" ]; then
  echo "verify: no packages for $fmt/$target in index/ - nothing to install" >&2
  exit 1
fi
echo "verify: $IMAGE -> snippet $snippet, packages: $pkgs" >&2

fpr="$(curl -fsSL "$BASE/lan-party.asc" | gpg --show-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')"
[ -n "$fpr" ] || { echo "cannot read fingerprint from $BASE/lan-party.asc" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
sed -e "s|@BASE_URL@|$BASE|g" -e "s|@SUITE@|$suite|g" -e "s|@FINGERPRINT@|$fpr|g" \
  "site/snippets/$snippet.sh" >"$tmp/snippet.sh"

# install_one: install a single package (exit code is kept)
# is_present:  package is unpacked/installed = download + signature check worked
case "$fam" in
  deb)
    prereq='export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq curl gnupg ca-certificates'
    refresh='apt-get update -qq'
    install_one='DEBIAN_FRONTEND=noninteractive apt-get install -y "$1"'
    is_present='dpkg-query -W -f="\${db:Status-Abbrev}" "$1" 2>/dev/null | grep -q "^i[iUFHWt]"' ;;
  rpm)
    prereq='dnf install -y gnupg2 ca-certificates; command -v curl >/dev/null || dnf install -y curl'
    refresh='dnf -y makecache'
    install_one='dnf install -y "$1"'
    is_present='rpm -q "$1" >/dev/null' ;;
  arch)
    # fresh containers have no pacman master key; real Arch installs always do
    prereq='pacman-key --init && pacman-key --populate archlinux && pacman -Syu --noconfirm --needed curl gnupg ca-certificates'
    refresh='pacman -Sy --noconfirm'
    install_one='pacman -S --noconfirm "$1"'
    is_present='pacman -Q "$1" >/dev/null 2>&1' ;;
  apk)
    prereq='apk add --no-cache curl gnupg ca-certificates'
    refresh='apk update'
    install_one='apk add "$1"'
    is_present='apk info -e "$1" >/dev/null' ;;
esac

cat >"$tmp/run.sh" <<RUN
#!/bin/sh
set -eu
PKGS="$pkgs"
install_one() { $install_one; }
is_present() { $is_present; }
$prereq
echo "--- repo setup (snippet) ---"
sh /verify/snippet.sh
echo "--- install ---"
$refresh
FAIL=0
for p in \$PKGS; do
  echo "--- \$p ---"
  if install_one "\$p"; then rc=0; else rc=\$?; fi
  if is_present "\$p"; then
    if [ "\$rc" = 0 ]; then echo "ok \$p"
    else echo "::warning title=$IMAGE::\$p was downloaded, verified and unpacked, but a package script failed (rc=\$rc)"; fi
  else
    echo "::error title=$IMAGE::\$p could not be installed from the repository"; FAIL=1
  fi
done
exit \$FAIL
RUN

docker run --rm --network host -v "$tmp:/verify:ro" "$IMAGE" sh /verify/run.sh
echo "verify OK: $IMAGE" >&2
