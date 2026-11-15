#!/usr/bin/env python3
"""sync.py plan|download  (see docs/DESIGN.md, pipeline steps 1 and 2)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import die, log, warn  # noqa: E402

API = "https://api.github.com"
CHECKSUM_FILES = ("SHA256SUMS", "checksums.txt")


def http_get(url: str, auth: bool, accept: str | None = None, retries: int = 4) -> bytes:
    headers = {"User-Agent": "lan-party-repo-sync"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GH_TOKEN")
    if auth and token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** attempt)
    die(f"GET {url} failed: {last}")


def fetch_releases(repo: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        data = json.loads(http_get(
            f"{API}/repos/{repo}/releases?per_page=100&page={page}", True,
            "application/vnd.github+json"))
        out.extend(data)
        if len(data) < 100:
            return out
        page += 1


def parse_sums(text: str) -> dict[str, str]:
    sums = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            sums[m.group(2)] = m.group(1).lower()
    return sums


def expected_assets(tools: list[dict], fmts: set[str]) -> list[dict]:
    items: dict[tuple[str, str, str], dict] = {}
    for tool in tools:
        log(f"releases of {tool['repo']}")
        for rel in fetch_releases(tool["repo"]):
            tag = rel["tag_name"]
            if rel.get("draft") or rel.get("prerelease"):
                continue
            if not re.search(tool["tag"], tag):
                log(f"  skip tag {tag} (no match)")
                continue
            assets = rel.get("assets") or []
            if not assets:
                log(f"  skip {tag}: no assets")
                continue
            sums: dict[str, str] | None = None
            for a in assets:
                for fmt in common.FORMATS:
                    rx = tool.get(fmt)
                    if fmt not in fmts or not rx or not re.search(rx, a["name"]):
                        continue
                    sha = None
                    digest = a.get("digest")
                    if digest and digest.startswith("sha256:"):
                        sha = digest[7:].lower()
                    else:
                        if sums is None:
                            sums = {}
                            for sa in assets:
                                if sa["name"] in CHECKSUM_FILES:
                                    sums = parse_sums(http_get(
                                        sa["browser_download_url"], False).decode())
                        sha = sums.get(a["name"])
                    key = (fmt, tool["name"], a["name"])
                    if key in items:
                        warn(f"duplicate asset {key} in {tag}; keeping the first")
                        continue
                    items[key] = {
                        "fmt": fmt, "tool": tool["name"], "tag": tag,
                        "version": tag[1:] if tag.startswith("v") else tag,
                        "asset": a["name"], "url": a["browser_download_url"],
                        "upstream_sha256": sha,
                    }
    return [items[k] for k in sorted(items)]


def cmd_plan(args: argparse.Namespace) -> int:
    only_tool = common.split_csv(args.only_tool)
    only_fmt = common.split_csv(args.only_fmt) or set(common.FORMATS)
    for f in only_fmt:
        if f not in common.FORMATS:
            die(f"unknown format {f}")
    tools = [t for t in common.load_tools() if not only_tool or t["name"] in only_tool]
    if only_tool and len(tools) != len(only_tool):
        die("unknown tool in --only-tool")

    expected = expected_assets(tools, only_fmt)
    index = {k: v for k, v in common.load_index().items()
             if k[0] in only_fmt and (not only_tool or k[1] in only_tool)}

    items: list[dict] = []
    counts = {"add": 0, "replace": 0, "remove": 0, "keep": 0}
    seen = set()
    for e in expected:
        key = (e["fmt"], e["tool"], e["asset"])
        seen.add(key)
        old = index.get(key)
        if args.full_rebuild or old is None:
            action = "add"
        elif e["upstream_sha256"] and old.get("upstream_sha256") != e["upstream_sha256"]:
            action = "replace"
            e["old_upstream_sha256"] = old.get("upstream_sha256")
        else:
            action = "keep"
        counts[action] += 1
        if action != "keep":
            items.append({**e, "action": action})
    for key, old in sorted(index.items()):
        if key not in seen:
            counts["remove"] += 1
            items.append({"action": "remove", "fmt": key[0], "tool": key[1],
                          "asset": key[2], "tag": old.get("tag"),
                          "pool_paths": old.get("pool_paths", [])})

    plan = {"full_rebuild": bool(args.full_rebuild), "counts": counts, "items": items}
    out = Path(args.out)
    common.write_json(out, plan)
    log(f"plan: +{counts['add']} ~{counts['replace']} -{counts['remove']} ={counts['keep']} -> {out}")
    changed = counts["add"] + counts["replace"] + counts["remove"]

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
            for k in ("add", "replace", "remove"):
                fh.write(f"{k}={counts[k]}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and (counts["replace"] or counts["remove"]):
        with open(summary, "a") as fh:
            fh.write("### Replacements / removals\n\n")
            for it in items:
                if it["action"] == "replace":
                    fh.write(f"- :warning: **replaced** `{it['fmt']}/{it['tool']}/{it['asset']}` "
                             f"(re-released, new sha256)\n")
                elif it["action"] == "remove":
                    fh.write(f"- :warning: **removed** `{it['fmt']}/{it['tool']}/{it['asset']}` "
                             f"(no longer on GitHub)\n")
            fh.write("\n")
    return 0


def _download_one(it: dict) -> tuple[dict, str | None]:
    dest = common.WORK / "dl" / it["fmt"] / it["tool"] / it["asset"]
    want = it.get("upstream_sha256")
    if dest.exists() and want and common.sha256_file(dest) == want:
        return it, None
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    data = http_get(it["url"], False)
    part.write_bytes(data)
    got = common.sha256_file(part)
    if want and got != want:
        part.unlink()
        die(f"sha256 mismatch for {it['asset']}: expected {want}, got {got}")
    part.replace(dest)
    return it, got


def cmd_download(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = common.read_json(plan_path)
    todo = [i for i in plan["items"] if i["action"] in ("add", "replace")]
    log(f"downloading {len(todo)} files")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_download_one, todo))
    dirty = False
    for it, got in results:
        if not it.get("upstream_sha256"):
            it["upstream_sha256"] = got or common.sha256_file(
                common.WORK / "dl" / it["fmt"] / it["tool"] / it["asset"])
            dirty = True
            log(f"computed sha256 for {it['asset']} (no upstream checksum)")
    if dirty:
        common.write_json(plan_path, plan)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--full-rebuild", action="store_true")
    p.add_argument("--only-tool", help="comma separated tool names (local testing)")
    p.add_argument("--only-fmt", help="comma separated formats, e.g. deb,arch")
    p.add_argument("--out", default=str(common.WORK / "plan.json"))
    p.set_defaults(fn=cmd_plan)
    d = sub.add_parser("download")
    d.add_argument("plan", nargs="?", default=str(common.WORK / "plan.json"))
    d.set_defaults(fn=cmd_download)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
