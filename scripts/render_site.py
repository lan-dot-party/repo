#!/usr/bin/env python3
"""Render the landing page and the static helper files into --out.

Usage: python3 -I scripts/render_site.py --out public [--base-url URL]
Stdlib only.
"""
import argparse
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
PLACEHOLDER_FPR = "NOT-YET-GENERATED"
DEFAULT_SUITE = "bookworm"

SUITE_LABELS = {
    "bookworm": "Debian 12 bookworm",
    "trixie": "Debian 13 trixie",
    "noble": "Ubuntu 24.04 noble",
    "resolute": "Ubuntu 26.04 resolute",
    "stable": "Other (stable — distribution-independent packages only)",
}
FORMATS = ["deb", "rpm", "arch", "apk"]
SNIPPETS = ["deb-list", "deb-sources", "rpm-el", "rpm-fedora", "arch", "alpine"]


def warn(msg):
    print("render_site: warning: " + msg, file=sys.stderr)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def esc(s):
    return html.escape(str(s), quote=True)


def load_toml(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return tomllib.load(f)


def version_key(tag):
    t = str(tag).lstrip("vV")
    parts = re.split(r"[^0-9]+", t)
    return tuple(int(p) for p in parts if p != "")


def load_index():
    """Returns list of fragment dicts (without the bulky 'fragment' field)."""
    out = []
    base = os.path.join(ROOT, "index")
    for fmt in FORMATS:
        d = os.path.join(base, fmt)
        if not os.path.isdir(d):
            continue
        for dirpath, _dirs, files in os.walk(d):
            for fn in sorted(files):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                        j = json.load(f)
                except (OSError, ValueError) as e:
                    warn("skipping %s: %s" % (fn, e))
                    continue
                j.pop("fragment", None)
                j.setdefault("fmt", fmt)
                out.append(j)
    return out


def get_fingerprint(asc_path):
    if not os.path.isfile(asc_path):
        warn("keys/lan-party.asc missing, using placeholder fingerprint")
        return PLACEHOLDER_FPR
    try:
        res = subprocess.run(
            ["gpg", "--show-keys", "--with-colons", asc_path],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        warn("gpg --show-keys failed (%s), using placeholder fingerprint" % e)
        return PLACEHOLDER_FPR
    for line in res.stdout.splitlines():
        f = line.split(":")
        if f[0] == "fpr" and len(f) > 9 and f[9]:
            return f[9].upper()
    warn("no fingerprint found in key file")
    return PLACEHOLDER_FPR


def pretty_fpr(fpr):
    if not re.fullmatch(r"[0-9A-F]{40}", fpr):
        return fpr
    groups = [fpr[i:i + 4] for i in range(0, 40, 4)]
    return " ".join(groups[:5]) + "  " + " ".join(groups[5:])


def code_block(text, tpl=None):
    attr = ' data-tpl="%s"' % esc(tpl) if tpl is not None else ""
    return '<div class="code"><pre%s><code>%s</code></pre></div>' % (attr, esc(text))


def build_tools(tools_cfg, entries):
    by_tool = {}
    for e in entries:
        by_tool.setdefault(e.get("tool"), []).append(e)
    result = []
    for t in tools_cfg.get("tool", []):
        name = t["name"]
        es = by_tool.get(name, [])
        tags = {}
        for e in es:
            tags.setdefault(str(e.get("tag", e.get("version", ""))), set()).add(e["fmt"])
        ordered = sorted(tags, key=lambda x: (version_key(x), x), reverse=True)
        latest = ordered[0] if ordered else None
        formats = [f for f in FORMATS if latest and f in tags[latest]]
        result.append({
            "name": name,
            "homepage": t.get("homepage", ""),
            "repo": t.get("repo", ""),
            "latest": latest.lstrip("vV") if latest else None,
            "versions": [v.lstrip("vV") for v in ordered],
            "formats": formats,
        })
    return result


def packages_table(tools):
    if not any(t["latest"] for t in tools):
        return '<p class="panel">No packages published yet.</p>'
    rows = []
    for t in tools:
        if not t["latest"]:
            continue
        badges = "".join('<span class="badge">%s</span>' % esc(f) for f in t["formats"])
        links = []
        if t["homepage"]:
            links.append('<a href="%s">Homepage</a>' % esc(t["homepage"]))
        if t["repo"]:
            links.append('<a href="https://github.com/%s">GitHub</a>' % esc(t["repo"]))
        rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (esc(t["name"]), esc(t["latest"]), badges, " &middot; ".join(links)))
    return ('<div class="table-wrap"><table>\n<thead><tr><th>Tool</th><th>Latest version</th>'
            '<th>Formats</th><th>Links</th></tr></thead>\n<tbody>\n%s\n</tbody>\n</table></div>'
            % "\n".join(rows))


def sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def render_setup(base_url, fpr, suites, snippets):
    tpl = read(os.path.join(SITE, "setup.sh"))
    defs = []
    for name in ("deb-sources", "rpm-el", "rpm-fedora", "arch", "alpine"):
        var = "SNIP_" + name.upper().replace("-", "_")
        defs.append("%s=%s" % (var, sh_quote(snippets[name])))
    # replace @SNIPPETS@ first, so the snippet text is not touched by the
    # other substitutions (it is already rendered), then the rest.
    marker = "\0SNIPPETS\0"
    tpl = tpl.replace("@SNIPPETS@", marker)
    tpl = (tpl.replace("@BASE_URL@", base_url).replace("@FINGERPRINT@", fpr)
              .replace("@SUITES@", " ".join(suites)))
    return tpl.replace(marker, "\n".join(defs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-url", default=os.environ.get("REPO_BASE_URL") or "https://repo.lan.party")
    a = ap.parse_args()
    base_url = a.base_url.rstrip("/")
    out = a.out

    tools_cfg = load_toml("tools.toml")
    distros = load_toml("distros.toml")
    suites = [s["name"] for s in distros.get("deb", {}).get("suite", [])]
    entries = load_index()
    fpr = get_fingerprint(os.path.join(ROOT, "keys", "lan-party.asc"))
    tools = build_tools(tools_cfg, entries)
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    # snippets: raw templates and rendered (suite kept as @SUITE@) variants
    raw = {n: read(os.path.join(SITE, "snippets", n + ".sh")) for n in SNIPPETS}
    rendered = {n: t.replace("@BASE_URL@", base_url).replace("@FINGERPRINT@", fpr)
                for n, t in raw.items()}

    def snip(n):
        if "@SUITE@" in rendered[n]:
            return code_block(rendered[n].replace("@SUITE@", DEFAULT_SUITE), rendered[n])
        return code_block(rendered[n])

    options = []
    for s in suites:
        label = SUITE_LABELS.get(s, s)
        sel = " selected" if s == DEFAULT_SUITE else ""
        options.append('                                <option value="%s"%s>%s</option>'
                       % (esc(s), sel, esc(label)))

    host_arch = "%s/arch/x86_64/<file>" % base_url
    subs = {
        "base_url": esc(base_url),
        "fingerprint": esc(fpr),
        "fingerprint_pretty": esc(pretty_fpr(fpr)),
        "suite_options": "\n".join(options),
        "quick_setup": code_block("curl -fsSL %s/setup.sh | sudo sh" % base_url),
        "snip_deb_sources": snip("deb-sources"),
        "snip_deb_list": snip("deb-list"),
        "snip_rpm_el": snip("rpm-el"),
        "snip_rpm_fedora": snip("rpm-fedora"),
        "snip_arch": snip("arch"),
        "snip_alpine": snip("alpine"),
        "code_apt_install": code_block("sudo apt install <tool>"),
        "code_dnf_install": code_block("sudo dnf install <tool>"),
        "code_pacman_install": code_block("sudo pacman -S <tool>"),
        "code_apk_install": code_block("sudo apk add <tool>"),
        "packages_table": packages_table(tools),
        "faq_reinstall": code_block(
            "sudo apt install --reinstall <pkg>\nsudo dnf reinstall <pkg>\n"
            "sudo pacman -Scc && sudo pacman -S <pkg>"),
        "faq_downgrade": code_block("sudo apt install <pkg>=VERSION"),
        "faq_arch_old": code_block("sudo pacman -U %s" % host_arch),
        "generated": esc(now.strftime("%Y-%m-%d %H:%M UTC")),
        "year": str(now.year),
    }
    page = read(os.path.join(SITE, "index.html.tmpl"))
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: subs[m.group(1)], page)

    os.makedirs(out, exist_ok=True)
    write(os.path.join(out, "index.html"), page)
    for fn in ("style.css", "app.js", ".htaccess"):
        shutil.copyfile(os.path.join(SITE, fn), os.path.join(out, fn))

    setup = render_setup(base_url, fpr, suites, rendered)
    write(os.path.join(out, "setup.sh"), setup)
    os.chmod(os.path.join(out, "setup.sh"), 0o755)

    write(os.path.join(out, "packages.json"), json.dumps({
        "generated": now.isoformat(),
        "base_url": base_url,
        "fingerprint": fpr,
        "tools": tools,
    }, indent=2) + "\n")

    asc = os.path.join(ROOT, "keys", "lan-party.asc")
    if os.path.isfile(asc):
        shutil.copyfile(asc, os.path.join(out, "lan-party.asc"))
        gpg_out = os.path.join(out, "lan-party.gpg")
        if os.path.exists(gpg_out):
            os.remove(gpg_out)
        try:
            subprocess.run(["gpg", "--batch", "--yes", "--dearmor", "-o", gpg_out, asc], check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            warn("gpg --dearmor failed: %s" % e)
    rsa = os.path.join(ROOT, "keys", "lan-party.rsa.pub")
    if os.path.isfile(rsa):
        shutil.copyfile(rsa, os.path.join(out, "lan-party.rsa.pub"))
    else:
        warn("keys/lan-party.rsa.pub missing, not published")
    print("render_site: wrote %s (%d tools, %d index entries)" % (out, len(tools), len(entries)))


if __name__ == "__main__":
    main()
