#!/usr/bin/env bash
# Sourced by sign-packages.sh / sign-metadata.sh. Sets up signing keys.
#
# Exports/sets: GNUPGHOME, GPG_OPTS (array), FPR, APK_KEY (private key file), DRY_RUN_KEY (0/1).
# Real mode: REPO_GPG_PRIVATE_KEY / APK_SIGNING_KEY are imported into temp locations.
# Dry-run mode (a secret is empty): a throwaway key is created once in work/dry-run/ and reused
# by later steps; work/.dry-run-key is written so deploy.sh refuses to deploy.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/work"
KEYS="$ROOT/keys"
DRY_DIR="$WORK/dry-run"
DRY_RUN_KEY=0
_KEYS_TMP="$(mktemp -d)"
trap 'rm -rf "$_KEYS_TMP"; gpgconf --kill gpg-agent >/dev/null 2>&1 || true' EXIT

_dry_banner() {
  {
    echo "################################################################"
    echo "#  DRY-RUN KEY: $1 is empty -> using a THROWAWAY key."
    echo "#  The result is NOT trustworthy and will NOT be deployed."
    echo "################################################################"
  } >&2
  mkdir -p "$WORK"
  : >"$WORK/.dry-run-key"
  DRY_RUN_KEY=1
}

setup_gpg() {
  GNUPGHOME="$_KEYS_TMP/gnupg"
  export GNUPGHOME
  mkdir -m 700 "$GNUPGHOME"
  GPG_OPTS=(--batch --yes --no-tty)
  if [ -n "${REPO_GPG_PASSPHRASE:-}" ]; then
    printf '%s' "$REPO_GPG_PASSPHRASE" >"$_KEYS_TMP/pass"
    chmod 600 "$_KEYS_TMP/pass"
    GPG_OPTS+=(--pinentry-mode loopback --passphrase-file "$_KEYS_TMP/pass")
  fi
  if [ -n "${REPO_GPG_PRIVATE_KEY:-}" ]; then
    if [[ "$REPO_GPG_PRIVATE_KEY" == *"BEGIN PGP"* ]]; then
      printf '%s\n' "$REPO_GPG_PRIVATE_KEY" >"$_KEYS_TMP/key.asc"
    else
      printf '%s' "$REPO_GPG_PRIVATE_KEY" | base64 -d >"$_KEYS_TMP/key.asc"
    fi
    gpg "${GPG_OPTS[@]}" --import "$_KEYS_TMP/key.asc" 2>/dev/null
  else
    _dry_banner REPO_GPG_PRIVATE_KEY
    mkdir -p "$DRY_DIR"
    if [ -f "$DRY_DIR/gpg-secret.asc" ]; then
      gpg --batch --import "$DRY_DIR/gpg-secret.asc" 2>/dev/null
    else
      gpg --batch --pinentry-mode loopback --passphrase '' \
        --quick-generate-key "lan.party DRY-RUN <dry-run@invalid>" rsa4096 sign never 2>/dev/null
      gpg --batch --pinentry-mode loopback --passphrase '' --armor --export-secret-keys \
        >"$DRY_DIR/gpg-secret.asc"
    fi
    # a throwaway key has no passphrase
    GPG_OPTS=(--batch --yes --no-tty)
  fi
  FPR="$(gpg --batch --list-secret-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')"
  [ -n "$FPR" ] || { echo "no secret key available in GNUPGHOME" >&2; exit 1; }
  mkdir -p "$KEYS"
  if [ ! -e "$KEYS/lan-party.asc" ]; then
    gpg --batch --armor --export "$FPR" >"$KEYS/lan-party.asc"
    echo "exported public key to keys/lan-party.asc ($FPR)" >&2
  fi
}

setup_apk_key() {
  APK_KEY="$_KEYS_TMP/apk.rsa"
  if [ -n "${APK_SIGNING_KEY:-}" ]; then
    printf '%s\n' "$APK_SIGNING_KEY" >"$APK_KEY"
  else
    _dry_banner APK_SIGNING_KEY
    mkdir -p "$DRY_DIR"
    [ -f "$DRY_DIR/apk.rsa" ] || openssl genrsa -out "$DRY_DIR/apk.rsa" 4096 2>/dev/null
    cp "$DRY_DIR/apk.rsa" "$APK_KEY"
  fi
  chmod 600 "$APK_KEY"
  mkdir -p "$KEYS"
  if [ ! -e "$KEYS/lan-party.rsa.pub" ]; then
    openssl rsa -in "$APK_KEY" -pubout -out "$KEYS/lan-party.rsa.pub" 2>/dev/null
    echo "exported public key to keys/lan-party.rsa.pub" >&2
  fi
}
