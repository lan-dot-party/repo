# lan.party package repository

A single signed package repository for all [lan.party](https://lan.party) tools, served at
<https://repo.lan.party>. It supports apt (deb), dnf/yum (rpm), pacman (Arch) and apk (Alpine).

Users set it up with `curl -fsSL https://repo.lan.party/setup.sh | sudo sh` or with the manual
steps on the landing page. Both use the same snippets from `site/snippets/`.

## How it works

GitHub releases of the tools are the source of truth. A GitHub Actions workflow
(`.github/workflows/publish.yml`) runs every 6 hours, on `repository_dispatch` (`tool-released`) and
manually. It syncs the releases listed in `tools.toml` against the fragment index in `index/`, downloads
new or changed assets, signs them, rebuilds all repository metadata from `index/`, verifies the result in
distribution containers, uploads everything over FTPS and finally commits `index/`.

Details: [docs/DESIGN.md](docs/DESIGN.md). Key handling: [KEYS.md](KEYS.md).

Layout: `tools.toml` (tools), `distros.toml` (suites and distro repos), `index/` (state),
`keys/` (public keys), `scripts/` (pipeline), `site/` (landing page sources, rendered by
`scripts/render_site.py`).

## Adding a tool

1. Add a `[[tool]]` block to `tools.toml` (name, GitHub repo, homepage, tag regex, `build` mode and one
   asset regex per format). See the existing blocks.
2. In the tool repository, add this job to the release workflow so the repo updates immediately:

```yaml
  notify-repo:
    needs: [publish]
    runs-on: ubuntu-latest
    steps:
      - name: Trigger repo.lan.party update
        env:
          GH_TOKEN: ${{ secrets.REPO_DISPATCH_TOKEN }}
        run: |
          if [ -z "$GH_TOKEN" ]; then echo "REPO_DISPATCH_TOKEN not set, the 6h schedule will pick it up"; exit 0; fi
          gh api repos/lan-dot-party/repo/dispatches -f event_type=tool-released \
            -f "client_payload[repo]=$GITHUB_REPOSITORY" -f "client_payload[tag]=$GITHUB_REF_NAME"
```

   Without `REPO_DISPATCH_TOKEN` the scheduled run picks up the release within 6 hours.

## Secrets

In this repository:

| Secret | Purpose |
| --- | --- |
| `REPO_GPG_PRIVATE_KEY` | armored private signing key |
| `REPO_GPG_PASSPHRASE` | its passphrase |
| `APK_SIGNING_KEY` | RSA private key for the Alpine APKINDEX |
| `FTP_HOST`, `FTP_USER`, `FTP_PASSWORD`, `FTP_PATH` | FTPS deploy target |

`REPO_DISPATCH_TOKEN` (a token that may call `repos/lan-dot-party/repo/dispatches`) is set in each tool repository.
See [KEYS.md](KEYS.md) for creating the keys.

## Manual runs

Actions, "publish", "Run workflow":

- `full_rebuild`: ignore `index/` and rebuild everything from GitHub.
- `dry_run`: build and verify against a local server, but do not deploy or commit `index/`.

## Rendering the site locally

```sh
python3 -I scripts/render_site.py --out /tmp/site [--base-url http://127.0.0.1:8765]
python3 -m http.server --directory /tmp/site 8765
```
