# repo.lan.party — design

One signed package repository for all lan.party tools, for apt (Debian/Ubuntu), dnf (Fedora/EL), pacman (Arch) and apk (Alpine).
Packages come from the GitHub releases of the tools listed in `tools.toml`. Hosting is a static web space that is reachable **only via FTPS** (lftp).
Every version that exists as a GitHub release is kept. A deleted release is removed, and a re-released asset (same file name, new hash) is replaced.

## Principles

- **The fragment index is the state.** `index/` (committed to git) holds one JSON file per published package file. The repository metadata (Packages, repodata, pacman db, APKINDEX) is **always rebuilt in full from `index/`**, so the package files are never needed for it. A package file goes over FTP once, and the next upload happens only when the file is replaced.
- GitHub is the source of truth. `sync` compares the current releases with `index/`.
- Python 3.11+ with the standard library only (`tomllib`, `urllib`, `gzip`, `tarfile`, `hashlib`, `xml.etree`). Always run as `python3 -I scripts/<x>.py`.
- External tools only on the GitHub runner (ubuntu-latest): `apt-ftparchive` (apt-utils), `createrepo_c`, `rpm`/`rpmsign`, `gpg`, `zstd`, `lftp`, `docker` (for `alpine` and the verify containers).
- Every signing step has a **throwaway mode**. Without the secrets `REPO_GPG_PRIVATE_KEY` and `APK_SIGNING_KEY`, a temporary key is generated (dry run). In that case nothing is deployed.

## Directories (relative to the repo root)

```
tools.toml, distros.toml     configuration (see the comments there)
index/<fmt>/<tool>/<asset>.json   fragment per package file (fmt = deb|rpm|arch|apk)
work/                        temporary (gitignored): work/plan.json, work/dl/<fmt>/<tool>/<asset>
public/                      build output = what goes to the webspace (gitignored)
site/                        landing page sources (template, CSS, setup.sh, .htaccess)
keys/lan-party.asc           public GPG key (armored), keys/lan-party.rsa.pub (apk)
scripts/                     pipeline
```

## Pipeline (the order in publish.yml)

1. `sync.py plan` → `work/plan.json`
   - Reads tools.toml and fetches **all** releases of each tool repo (`GET /repos/{repo}/releases?per_page=100`, paginated, `GH_TOKEN`). Ignores drafts and pre-releases. The tag must match `tag`.
   - For each asset that matches one of the regexes `deb|rpm|arch|apk`: expected = `{fmt, tool, tag, version, asset, url, upstream_sha256}`. `upstream_sha256` comes from the asset's API field `digest` (`sha256:…`). If that is missing, it comes from `SHA256SUMS`/`checksums.txt` in the same release, and as a last resort it is computed after the download.
   - Comparison with `index/`: `add` (new), `replace` (same `fmt/tool/asset`, different `upstream_sha256`), `remove` (in the index but no longer on GitHub), `keep`.
   - `--full-rebuild`: everything becomes `add` (index/ is ignored and rebuilt).
2. `sync.py download work/plan.json` → loads `add`+`replace` to `work/dl/<fmt>/<tool>/<asset>` and verifies the sha256 (abort on a mismatch).
3. `sign-packages.sh work/dl`
   - rpm: `rpmsign --addsign` (`%_gpg_name` = key fingerprint) in place.
   - arch: `gpg --detach-sign --no-armor` → `<asset>.sig`.
   - apk: no re-signing for now. The spike checks whether apk accepts unsigned packages from a signed APKINDEX. If not, `abuild-sign` runs in an `alpine` container.
4. `fragment.py work/plan.json` → writes/replaces `index/<fmt>/<tool>/<asset>.json` for add/replace and deletes them for remove. Fields (all formats):
   ```json
   {"fmt":"deb","tool":"lanpilot","tag":"1.0.0","version":"1.0.0-1~debian12","asset":"lanpilot_1.0.0-1.debian12_amd64.deb",
    "upstream_sha256":"…","sha256":"… (after signing)","size":123,"arch":"amd64",
    "pool_paths":["deb/pool/lanpilot/lanpilot_1.0.0-1.debian12_amd64.deb"],
    "targets":["bookworm"],          // deb: suites; rpm: repo paths; arch/apk: [] (all)
    "fragment": { … format-specific … }}
   ```
   - **deb**: `fragment.stanza` = output of `apt-ftparchive packages` for this one file, with `Filename: pool/<tool>/<asset>` (relative to `deb/`). `arch` from the control file (`all` is allowed).
   - **rpm**: `createrepo_c` on a directory with only this rpm. Then `fragment.primary`/`filelists`/`other` = the respective `<package>` element as an XML string. In `primary` the location is rewritten to `<location xml:base="https://repo.lan.party/rpm/pool/<tool>/" href="<asset>"/>`. The base URL is configurable through the env `REPO_BASE_URL`, default `https://repo.lan.party`.
   - **arch**: pure Python. `zstd -dc` → tarfile, read `.PKGINFO`, build the `desc` text the way `repo-add` does (%FILENAME%, %NAME%, %BASE%, %VERSION%, %DESC%, %CSIZE%, %ISIZE%, %SHA256SUM%, %PGPSIG% (base64 of the .sig), %URL%, %LICENSE%, %ARCH%, %BUILDDATE%, %PACKAGER%, %DEPENDS%, %OPTDEPENDS%, %MAKEDEPENDS%, %CONFLICTS%, %PROVIDES%, %REPLACES%, %BACKUP% where present). `fragment.desc`, `fragment.files` (`%FILES%` list without the dot-files), `fragment.name`, `fragment.pkgver`. `arch` from .PKGINFO (`x86_64`/`aarch64`/`armv7h`/`any`).
   - **apk**: one `docker run --rm -v …:/w alpine:3.21 apk index --allow-untrusted -o /w/out/<asset>.tar.gz /w/in/<asset>` for each batch. Extract the stanza from the generated APKINDEX → `fragment.stanza`. `arch` from `A:`. The pool path is `alpine/<branch>/<arch>/<asset>`, because apk expects the files next to the APKINDEX. Each `apk` asset gets a normalised file name `<P>-<V>.apk` (the apk convention). The original name stays in `asset`.
   - **Distro targets**: the regex in tools.toml contains the group `(?P<distro>…)` for `build = "per_distro"`. That group is matched against `distros.toml` `[[deb.suite]].tags` / `[[rpm.repo]].tags`. A `generic` package goes into **every** suite/repo, including `stable`/`generic`. If a per_distro package has no match, the step aborts with an error.
   - Pool paths: deb `deb/pool/<tool>/<asset>`, rpm `rpm/pool/<tool>/<asset>`, arch `arch/<arch>/<asset>`, apk `alpine/<branch>/<arch>/<P>-<V>.apk`.
5. `assemble.py --out public` builds the metadata **entirely from index/**:
   - deb: per suite and architecture in distros.toml, `public/deb/dists/<suite>/main/binary-<arch>/Packages` + `.gz` (stanzas of the packages with this arch, plus `all`, with target = suite). Then `apt-ftparchive release` with Origin `lan.party`, Label `lan.party`, Suite/Codename = suite, Architectures, Components `main` → `Release`.
   - rpm: per repo path, `public/rpm/<path>/repodata/{primary,filelists,other}.xml.gz` + `repomd.xml` (sha256 checksum, open-checksum, size, open-size, timestamp, revision), each with correct namespaces and a `packages="N"` attribute.
   - arch: per architecture, the **newest** version of each package name (`vercmp` logic in Python: epoch:pkgver-pkgrel, alpha-numeric segments like libalpm). `any` packages go into every architecture. `public/arch/<arch>/lan-party.db.tar.gz` (`<name>-<ver>/desc`), `lan-party.files.tar.gz` (`desc` + `files`), plus copies `lan-party.db` and `lan-party.files` (no symlinks over FTP). pacman loads `<Server>/<%FILENAME%>`, so arch packages sit **next to the db**: `pool_path` = `arch/<arch>/<asset>` (+ `.sig`). An `any` package gets one copy per architecture, and `pool_path` becomes a list in that case (the field is then `pool_paths`, used for every format as a list).
   - apk: per architecture `APKINDEX` (all stanzas with that `A:` + noarch) → `APKINDEX.tar.gz` (gzip tar with `APKINDEX` + `DESCRIPTION`), signed in step 6.
   - Places the files from `work/dl` (new/replaced only) under `public/<pool_path>`.
   - Writes `public/deploy-plan.json`: `{"upload": [pool paths], "delete": [pool paths of removed packages], "metadata": [all metadata files]}`.
6. `sign-metadata.sh public`
   - deb: `gpg --clearsign` → `InRelease`, `gpg -abs` → `Release.gpg`.
   - rpm: `gpg --detach-sign --armor` → `repodata/repomd.xml.asc`.
   - arch: `gpg --detach-sign` for `lan-party.db(.tar.gz)` and `lan-party.files(.tar.gz)` → `.sig` (copies as well).
   - apk: the abuild signature format. `openssl dgst -sha1 -sign key` over the gzipped `APKINDEX.tar.gz` stream → a tar segment `.SIGN.RSA.lan-party.rsa.pub` as a separate gzip stream **in front of** the index stream.
7. `render_site.py --out public` → `public/index.html`, `public/packages.json`, `public/style.css`, `public/setup.sh`, `public/.htaccess`, `public/lan-party.asc`, `public/lan-party.gpg` (dearmored), `public/lan-party.rsa.pub`. Reads index/, tools.toml, distros.toml and keys/. The fingerprint comes from `gpg --show-keys --with-colons keys/lan-party.asc`.
8. `verify` (see `scripts/verify.sh <base-url> <distro-image>`): the commands are **the same ones the landing page shows** (taken from `site/snippets/*.sh` so they are never duplicated). Repo setup → update → install all packages available for that distro → `<tool> --version` or at least `dpkg -s`/`rpm -q`/`pacman -Q`/`apk info -e`.
   - Before deploy (dry run/full rebuild): `python3 -m http.server -d public 8080`, base URL `http://127.0.0.1:8080`, containers with `--network host`.
   - After deploy: against `https://repo.lan.party`.
9. `deploy.sh public` (lftp, FTPS: `set ftp:ssl-force true; set ftp:ssl-protect-data true; set ssl:verify-certificate yes`). Order: (a) pool files from `upload`, (b) metadata + landing page, (c) `rm` for `delete`. Env: `FTP_HOST`, `FTP_USER`, `FTP_PASSWORD`, `FTP_PATH`.
10. Commit `index/` (only after a successful deploy). Commit message: `index: +N ~M -K`. No-op when nothing changed.

## Landing page (site/)

- Static, no CDN, no external JS. Look of the lan.party website: background `#0f0f0f`, text white, panels `#2c3e50`, links `#3498db`/hover `#2980b9`, accent `#4682b4`, Arial, line-height 1.6, `.container` max 1200px, radius 8px, logo `https://lan.party/Dot_Transparent-White.png` (height 40px). English (like lan.party).
- Content: header, a short intro ("one repo for all lan.party tools, signed"), fingerprint, tabs **Debian/Ubuntu** (one-line `.list` and **deb822** `.sources`, a suite dropdown that updates the snippets, a hint about `stable`), **Fedora / RHEL / Rocky** (`.repo` for EL and Fedora with `$releasever`, `gpgcheck=1`, `repo_gpgcheck=1`), **Arch** (`pacman-key --add` + `--lsign-key`, `[lan-party]` snippet), **Alpine** (key to `/etc/apk/keys/`, repository line), and "Quick setup" `curl -fsSL https://repo.lan.party/setup.sh | sudo sh`. Copy buttons, works without JS (the snippets are in the HTML, JS only for tabs/dropdown/copy).
- A package table generated from index/: tool, newest version, formats, link to the homepage/GitHub.
- Re-releases and removed versions: a short FAQ section ("Why didn't I get the fixed 1.3.0?" → `apt install --reinstall`).
- Mobile: 16px side gutter, no horizontal scroll (the code blocks scroll internally).

## Implementation notes

Deviations and additions made while implementing the pipeline:

- `scripts/_keys.sh` (sourced by `sign-packages.sh` and `sign-metadata.sh`) holds the shared key setup. The throwaway (dry-run) keys are created once in `work/dry-run/` and reused by later steps, so packages and metadata are signed with the same key. `work/.dry-run-key` is the marker `deploy.sh` checks. A throwaway public key is exported to `keys/lan-party.asc` / `keys/lan-party.rsa.pub` only if those files do not exist. If a real key is committed in `keys/` and the secrets are missing, a dry run signs with a key that does not match it (verify fails, which is the intended signal).
- `REPO_GPG_PRIVATE_KEY` may be armored or base64 encoded.
- Fragments have two extra top-level fields: `pkgname` (package name from the control file / rpm / .PKGINFO / `P:`) and, for apk, `fragment.filename` (normalised `<P>-<V>.apk`). `version` is the package-manager version (`ver-rel`, with `epoch:` for rpm if non-zero). `pool_paths` for arch also lists the `.sig` files, so they are uploaded/deleted together with the package. `assemble.py` takes the source of a `.sig` pool path from `work/dl/arch/<tool>/<asset>.sig`.
- `sync.py plan`: `--only-tool` and `--only-fmt` take comma-separated lists and also scope the comparison against `index/`. With `--full-rebuild` every expected asset is `add`, but index entries that no longer exist on GitHub are still reported as `remove` so that their pool files get deleted. If an asset has neither a `digest` nor a checksum file entry, `upstream_sha256` is null in the plan; `download` computes it and writes it back into `plan.json` (no replace detection is possible for such assets until it is in the index). Downloads use the plain `browser_download_url` without the `Authorization` header (the redirect goes to another host). `plan` writes `changed/add/replace/remove` to `$GITHUB_OUTPUT` and the replace/remove warnings to `$GITHUB_STEP_SUMMARY`.
- `assemble.py` wipes `--out` first. Pool files are only copied for `add`/`replace` items of the plan. `public/deploy-plan.json` `metadata` only lists what `assemble.py` produced; `deploy.sh` uploads every other file in `public/` (signatures, landing page) as metadata as well, entry points (`InRelease`, `Release`, `repomd.xml`, `*.db`, `APKINDEX.tar.gz`, `index.html`) last. rpm metadata files use plain names (`primary.xml.gz`, ...), so no stale hashed files accumulate on the webspace.
- Package files are never deleted if another index entry still references the same pool path.
- `verify.sh` gets the fingerprint from `<base-url>/lan-party.asc`. Package names come from `python3 -I scripts/common.py packages-for <deb|rpm|arch|apk> <suite|repo path|x86_64>` (amd64/x86_64 plus `all`/`noarch`/`any`).
- `publish.yml` is split into jobs (build, verify-pre, deploy, verify-post) so the pre-deploy matrix can gate the deploy. `public/` and the new `index/` travel as artifacts; the deploy job commits the new `index/` after the FTP upload. `can_deploy` is false for dry runs, throwaway keys or missing secrets.
- Not testable on the dev machine (no rpm, createrepo_c, docker, lftp): rpm signing/fragments, apk fragments and the apk index signature, the verify containers and the lftp upload. The rpm/apk assemblers and the apk signature wrapper were only tested with synthetic fragments.
