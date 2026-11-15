# Signing keys

The repository uses two keys:

| Key | Signs | Private part | Public part |
|---|---|---|---|
| GPG, RSA 4096 | deb `InRelease`, rpm packages + `repomd.xml`, Arch packages + db | secret `REPO_GPG_PRIVATE_KEY` (+ `REPO_GPG_PASSPHRASE`) | `keys/lan-party.asc` |
| RSA 4096 (OpenSSL) | Alpine `APKINDEX` | secret `APK_SIGNING_KEY` | `keys/lan-party.rsa.pub` |

Create them on a trusted machine (Linux or macOS with `gnupg` and `openssl`) inside a checkout of this
repository, in a temporary `GNUPGHOME`, never in your normal keyring. RSA 4096 is used on purpose: it
works with every supported rpm/apt/pacman version, including EL9.

## 1. GPG key

```sh
git clone git@github.com:lan-dot-party/repo.git && cd repo
export GNUPGHOME="$(mktemp -d)"; chmod 700 "$GNUPGHOME"

# asks for the passphrase interactively (nothing ends up in the shell history)
gpg --quick-generate-key "LAN.party Repository Signing Key <repo@lan.party>" rsa4096 sign 5y

FPR=$(gpg --list-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')
echo "$FPR"
```

Export (asks for the passphrase again):

```sh
gpg --armor --export-secret-keys "$FPR" > repo-private.asc   # -> secret REPO_GPG_PRIVATE_KEY + offline backup
gpg --armor --export "$FPR" > keys/lan-party.asc             # -> commit
cp "$GNUPGHOME/openpgp-revocs.d/$FPR.rev" revoke.asc         # revocation certificate -> offline backup
```

## 2. Alpine RSA key

The file name must stay `lan-party.rsa.pub`: the APKINDEX signature (`.SIGN.RSA.lan-party.rsa.pub`)
refers to it, and clients store it under that name in `/etc/apk/keys/`.

```sh
openssl genrsa -out lan-party.rsa 4096                          # -> secret APK_SIGNING_KEY + offline backup
openssl rsa -in lan-party.rsa -pubout -out keys/lan-party.rsa.pub   # -> commit
```

## 3. GitHub secrets

Secrets can only be set by the repository owner (`lan-dot-party`), either in
*Settings → Secrets and variables → Actions* or with `gh` logged in as `lan-dot-party`:

```sh
gh secret set REPO_GPG_PRIVATE_KEY -R lan-dot-party/repo < repo-private.asc
gh secret set REPO_GPG_PASSPHRASE  -R lan-dot-party/repo          # prompts for the value
gh secret set APK_SIGNING_KEY      -R lan-dot-party/repo < lan-party.rsa
```

## 4. Commit the public keys

```sh
git add keys/lan-party.asc keys/lan-party.rsa.pub
git commit -m "Add repository signing keys" && git push
```

## 5. Backup and cleanup

GitHub secrets cannot be read back. **Keep an offline backup** of `repo-private.asc`, the passphrase,
`lan-party.rsa` and `revoke.asc` (password manager, encrypted USB stick). Without it the key can
neither be extended nor revoked. Then:

```sh
shred -u repo-private.asc lan-party.rsa revoke.asc   # on macOS: rm -P
rm -rf "$GNUPGHOME"; unset GNUPGHOME
```

## Extending the expiry

Before the key expires (5 years), import the backup into a temporary `GNUPGHOME` and run:

```sh
gpg --import repo-private.asc
gpg --quick-set-expire "$FPR" 5y
gpg --armor --export "$FPR" > keys/lan-party.asc
gpg --armor --export-secret-keys "$FPR" > repo-private.asc   # update secret + backup
```

Commit the new `keys/lan-party.asc` and update `REPO_GPG_PRIVATE_KEY`. The fingerprint stays the same, so
clients only need to refresh the key (`apt`: re-download `lan-party.gpg`; `dnf`: `rpm --import` the new
`lan-party.asc`; `pacman`: `pacman-key --add` again).

## Rotation

1. Generate a new key as above and publish the new public key next to the old one.
2. Switch the GitHub secrets to the new key; the next publish signs everything with it.
3. Update `keys/lan-party.asc` and announce the new fingerprint on the landing page and in the release notes.
4. Users re-run the setup (`curl -fsSL https://repo.lan.party/setup.sh | sudo sh`) or re-import the key.
5. Revoke the old key with its revocation certificate once everybody has migrated, and publish the revocation.
