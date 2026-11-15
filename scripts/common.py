#!/usr/bin/env python3
"""Shared helpers for the repo.lan.party build pipeline (standard library only).

Run as `python3 -I scripts/common.py <cmd>` for the small CLI helpers:
  packages-for <fmt> <target>      package names available for a verify target
  apk-prepend-sig <sig> <idx.tar.gz> <keyname> <out>   build a signed APKINDEX.tar.gz
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tomllib
from functools import cmp_to_key
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
FORMATS = ("deb", "rpm", "arch", "apk")
INDEX_DIR = Path(os.environ.get("INDEX_DIR", ROOT / "index"))


# --------------------------------------------------------------------------- logging
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    log(f"WARNING: {msg}")


def die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    log(f"ERROR: {msg}")
    sys.exit(code)


def base_url() -> str:
    return os.environ.get("REPO_BASE_URL", "https://repo.lan.party").rstrip("/")


# --------------------------------------------------------------------------- config
def load_toml(name: str) -> dict:
    with open(ROOT / name, "rb") as fh:
        return tomllib.load(fh)


def load_tools() -> list[dict]:
    return load_toml("tools.toml")["tool"]


def load_distros() -> dict:
    return load_toml("distros.toml")


def split_csv(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {v.strip() for v in value.split(",") if v.strip()}


# --------------------------------------------------------------------------- files
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def gzip_bytes(data: bytes) -> bytes:
    """Deterministic gzip (mtime 0)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(data)
    return buf.getvalue()


# --------------------------------------------------------------------------- index
def index_path(fmt: str, tool: str, asset: str) -> Path:
    return INDEX_DIR / fmt / tool / (asset + ".json")


def load_index() -> dict[tuple[str, str, str], dict]:
    """All fragments, keyed by (fmt, tool, asset)."""
    out: dict[tuple[str, str, str], dict] = {}
    for fmt in FORMATS:
        for p in sorted((INDEX_DIR / fmt).glob("*/*.json")):
            entry = read_json(p)
            out[(entry["fmt"], entry["tool"], entry["asset"])] = entry
    return out


# --------------------------------------------------------------------------- pacman vercmp
def _rpmvercmp(a: str, b: str) -> int:
    """Port of libalpm's rpmvercmp."""
    if a == b:
        return 0
    i = j = 0
    la, lb = len(a), len(b)
    while i < la and j < lb:
        p1, p2 = i, j
        while i < la and not a[i].isalnum():
            i += 1
        while j < lb and not b[j].isalnum():
            j += 1
        if i >= la or j >= lb:
            break
        if (i - p1) != (j - p2):
            return -1 if (i - p1) < (j - p2) else 1
        s1, s2 = i, j
        if a[i].isdigit():
            while i < la and a[i].isdigit():
                i += 1
            while j < lb and b[j].isdigit():
                j += 1
            isnum = True
        else:
            while i < la and a[i].isalpha():
                i += 1
            while j < lb and b[j].isalpha():
                j += 1
            isnum = False
        seg1, seg2 = a[s1:i], b[s2:j]
        if not seg2:
            return 1 if isnum else -1
        if isnum:
            seg1, seg2 = seg1.lstrip("0"), seg2.lstrip("0")
            if len(seg1) != len(seg2):
                return 1 if len(seg1) > len(seg2) else -1
        if seg1 != seg2:
            return 1 if seg1 > seg2 else -1
    if i >= la and j >= lb:
        return 0
    if (i >= la and not (j < lb and b[j].isalpha())) or (i < la and a[i].isalpha()):
        return -1
    return 1


def _parse_evr(s: str) -> tuple[str, str, str | None]:
    m = re.match(r"^(\d+):", s)
    epoch = "0"
    if m:
        epoch = m.group(1)
        s = s[m.end():]
    if "-" in s:
        ver, rel = s.rsplit("-", 1)
        return epoch, ver, rel
    return epoch, s, None


def vercmp(a: str, b: str) -> int:
    """pacman's vercmp: epoch:pkgver-pkgrel."""
    if a == b:
        return 0
    ea, va, ra = _parse_evr(a)
    eb, vb, rb = _parse_evr(b)
    rc = _rpmvercmp(ea, eb)
    if rc == 0:
        rc = _rpmvercmp(va, vb)
        if rc == 0 and ra is not None and rb is not None:
            rc = _rpmvercmp(ra, rb)
    return rc


vercmp_key = cmp_to_key(vercmp)


# --------------------------------------------------------------------------- apk signature wrapper
def apk_prepend_sig(sig: bytes, index_gz: bytes, keyname: str) -> bytes:
    """abuild-sign layout: gzip(tar(.SIGN.RSA.<key>) without end blocks) + index gzip stream."""
    name = f".SIGN.RSA.{keyname}"
    ti = tarfile.TarInfo(name)
    ti.size = len(sig)
    ti.mode = 0o644
    ti.mtime = 0
    ti.uname = ti.gname = "root"
    raw = ti.tobuf(tarfile.GNU_FORMAT) + sig
    raw += b"\0" * (-len(raw) % 512)  # pad to block, but NO end-of-archive blocks
    return gzip_bytes(raw) + index_gz


# --------------------------------------------------------------------------- packages-for
def packages_for(fmt: str, target: str) -> list[str]:
    """Package names (newest not required) a client of `target` can install."""
    names: set[str] = set()
    for (f, _tool, _asset), e in load_index().items():
        if f != fmt:
            continue
        arch = e.get("arch")
        if fmt == "deb":
            ok = target in e["targets"] and arch in ("amd64", "all")
        elif fmt == "rpm":
            ok = target in e["targets"] and arch in ("x86_64", "noarch")
        elif fmt == "arch":
            ok = arch in ("x86_64", "any")
        else:
            ok = arch in ("x86_64", "noarch")
        if ok:
            names.add(e.get("pkgname") or e["tool"])
    return sorted(names)


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[1] == "packages-for":
        print(" ".join(packages_for(argv[2], argv[3])))
        return 0
    if len(argv) == 6 and argv[1] == "apk-prepend-sig":
        sig = Path(argv[2]).read_bytes()
        idx = Path(argv[3]).read_bytes()
        Path(argv[5]).write_bytes(apk_prepend_sig(sig, idx, argv[4]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
