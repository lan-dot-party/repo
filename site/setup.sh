#!/bin/sh
# lan.party package repository - quick setup.
# Usage: wget -qO- @BASE_URL@/setup.sh | sudo sh
#    or: curl -fsSL @BASE_URL@/setup.sh | sudo sh
# The snippets below are generated from site/snippets/*.sh and are exactly the
# commands shown on the landing page.

# Fail early: abort on any error and on unset variables. pipefail where the
# shell supports it (dash >= 0.5.12, busybox ash, bash). No -x: run
# "sh -x setup.sh" yourself if you want to trace it.
set -eu
# shellcheck disable=SC3040
(set -o pipefail) 2>/dev/null && set -o pipefail

BASE_URL='@BASE_URL@'
KNOWN_SUITES='@SUITES@'
MANUAL_URL="$BASE_URL/#manual"

@SNIPPETS@

die() {
    printf 'lan.party setup: %s\n' "$*" >&2
    exit 1
}

run_snippet() {
    # $1 = snippet text, $2 = deb suite (only used by the deb snippets)
    printf '%s\n' "$1" | sed "s|@SUITE@|$2|g" | sh -eu
}

# need <command> <install hint>: stop before running a snippet that would fail
need() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed. Install it first: $2"
}

has_word() {
    case " $IDS " in
        *" $1 "*) return 0 ;;
    esac
    return 1
}

main() {
    [ "$(id -u)" -eq 0 ] || die "please run as root (wget -qO- $BASE_URL/setup.sh | sudo sh)"
    [ -r /etc/os-release ] || die "/etc/os-release not found, see $MANUAL_URL"

    . /etc/os-release
    IDS="${ID:-} ${ID_LIKE:-}"

    if [ "${ID:-}" = fedora ]; then
        target=rpm-fedora
    elif has_word rhel || has_word centos || has_word rocky || has_word almalinux; then
        target=rpm-el
    elif has_word fedora; then
        target=rpm-fedora
    elif has_word arch || has_word manjaro || has_word endeavouros; then
        target=arch
    elif has_word alpine; then
        target=alpine
    elif has_word debian || has_word ubuntu; then
        target=deb
    else
        printf 'lan.party setup: unsupported distribution (%s).\n' "${PRETTY_NAME:-${ID:-unknown}}" >&2
        printf 'Please follow the manual instructions: %s\n' "$MANUAL_URL" >&2
        exit 1
    fi

    printf 'Detected: %s -> %s\n' "${PRETTY_NAME:-${ID:-unknown}}" "$target"

    case "$target" in
        deb)
            suite="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
            found=
            for s in $KNOWN_SUITES; do
                [ "$s" = "$suite" ] && found=1
            done
            if [ -z "$found" ]; then
                printf 'Warning: codename "%s" is not served by this repository, using "stable" (only distribution-independent packages).\n' "$suite" >&2
                suite=stable
            fi
            need wget "apt-get install wget ca-certificates"
            run_snippet "$SNIP_DEB_SOURCES" "$suite"
            ;;
        rpm-el) run_snippet "$SNIP_RPM_EL" "" ;;
        rpm-fedora) run_snippet "$SNIP_RPM_FEDORA" "" ;;
        arch)
            need curl "pacman -S curl"
            run_snippet "$SNIP_ARCH" ""
            ;;
        alpine)
            need wget "apk add wget"
            run_snippet "$SNIP_ALPINE" ""
            ;;
    esac || die "setting up the repository failed (see the error above), manual steps: $MANUAL_URL"

    printf 'Done. Browse the available packages at %s/\n' "$BASE_URL"
}

main "$@"
