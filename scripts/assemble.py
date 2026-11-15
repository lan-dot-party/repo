#!/usr/bin/env python3
"""assemble.py --out public: build all repository metadata from index/ (pipeline step 5)."""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import die, log, warn  # noqa: E402

DL = common.WORK / "dl"


def write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


# --------------------------------------------------------------------------- deb
def deb_meta(out: Path, entries: list[dict], distros: dict) -> None:
    if shutil.which("apt-ftparchive") is None:
        die("apt-ftparchive not installed (apt install apt-utils)")
    cfg = distros["deb"]
    comp, archs = cfg["component"], cfg["architectures"]
    for suite in (s["name"] for s in distros["deb"]["suite"]):
        sdir = out / "deb" / "dists" / suite
        for arch in archs:
            sel = [e for e in entries if suite in e["targets"] and e["arch"] in (arch, "all")]
            sel.sort(key=lambda e: (e["pkgname"], e["asset"]))
            seen = set()
            for e in sel:
                k = (e["pkgname"], e["version"], e["arch"])
                if k in seen:
                    warn(f"{suite}/{arch}: duplicate {k} ({e['asset']})")
                seen.add(k)
            text = "".join(e["fragment"]["stanza"].strip("\n") + "\n\n" for e in sel)
            bdir = sdir / comp / f"binary-{arch}"
            write(bdir / "Packages", text)
            write(bdir / "Packages.gz", common.gzip_bytes(text.encode()))
        o = "APT::FTPArchive::Release::"
        r = subprocess.run(
            ["apt-ftparchive", "-o", f"{o}Origin=lan.party", "-o", f"{o}Label=lan.party",
             "-o", f"{o}Suite={suite}", "-o", f"{o}Codename={suite}",
             "-o", f"{o}Architectures={' '.join(archs)}", "-o", f"{o}Components={comp}",
             "release", str(sdir)], capture_output=True, text=True)
        if r.returncode:
            die(f"apt-ftparchive release failed: {r.stderr}")
        write(sdir / "Release", r.stdout)


# --------------------------------------------------------------------------- rpm
NS = {
    "primary": ('metadata', 'xmlns="http://linux.duke.edu/metadata/common" '
                'xmlns:rpm="http://linux.duke.edu/metadata/rpm"'),
    "filelists": ('filelists', 'xmlns="http://linux.duke.edu/metadata/filelists"'),
    "other": ('otherdata', 'xmlns="http://linux.duke.edu/metadata/other"'),
}


def _relocate(primary: str, href: str) -> str:
    esc = href.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    new, n = re.subn(r'<location [^>]*/>', f'<location href="{esc}"/>', primary, count=1)
    if n != 1:
        raise SystemExit(f"no <location> in rpm fragment for {href}")
    return new


def rpm_meta(out: Path, entries: list[dict], distros: dict) -> None:
    for repo in distros["rpm"]["repo"]:
        path = repo["path"]
        sel = sorted((e for e in entries if path in e["targets"]),
                     key=lambda e: (e["pkgname"], e["asset"]))
        rdir = out / "rpm" / path / "repodata"
        ts = int(time.time())
        data_xml = []
        for kind, (root, ns) in NS.items():
            if kind == "primary":
                # Location relative to this repo's baseurl (rpm/<path>/), so the same
                # metadata works on the local verify server and on repo.lan.party.
                up = "../" * len(path.strip("/").split("/"))
                body = "".join(_relocate(e["fragment"][kind], up + e["pool_paths"][0][len("rpm/"):]) + "\n"
                               for e in sel)
            else:
                body = "".join(e["fragment"][kind] + "\n" for e in sel)
            xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<{root} {ns} packages="{len(sel)}">\n'
                   f'{body}</{root}>\n').encode("utf-8")
            ET.fromstring(xml)  # well-formedness check
            gz = common.gzip_bytes(xml)
            write(rdir / f"{kind}.xml.gz", gz)
            data_xml.append(
                f'  <data type="{kind}">\n'
                f'    <checksum type="sha256">{hashlib.sha256(gz).hexdigest()}</checksum>\n'
                f'    <open-checksum type="sha256">{hashlib.sha256(xml).hexdigest()}</open-checksum>\n'
                f'    <location href="repodata/{kind}.xml.gz"/>\n'
                f'    <timestamp>{ts}</timestamp>\n'
                f'    <size>{len(gz)}</size>\n'
                f'    <open-size>{len(xml)}</open-size>\n'
                f'  </data>\n')
        write(rdir / "repomd.xml",
              '<?xml version="1.0" encoding="UTF-8"?>\n'
              '<repomd xmlns="http://linux.duke.edu/metadata/repo" '
              'xmlns:rpm="http://linux.duke.edu/metadata/rpm">\n'
              f'  <revision>{ts}</revision>\n' + "".join(data_xml) + '</repomd>\n')


# --------------------------------------------------------------------------- arch
def add_tar_file(tf: tarfile.TarFile, name: str, data: bytes | None) -> None:
    ti = tarfile.TarInfo(name)
    ti.mtime = 0
    ti.uname = ti.gname = "root"
    if data is None:
        ti.type, ti.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(ti)
    else:
        ti.size, ti.mode = len(data), 0o644
        tf.addfile(ti, io.BytesIO(data))


def arch_meta(out: Path, entries: list[dict], distros: dict) -> list[tuple[str, str, str]]:
    """Returns the chosen (arch, name, version) list for reporting."""
    repo = distros["arch"]["repo_name"]
    chosen = []
    for arch in distros["arch"]["architectures"]:
        best: dict[str, dict] = {}
        for e in entries:
            if e["arch"] not in (arch, "any"):
                continue
            cur = best.get(e["pkgname"])
            if cur is None or common.vercmp(e["version"], cur["version"]) > 0:
                best[e["pkgname"]] = e
        for kind, with_files in (("db", False), ("files", True)):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
                for name in sorted(best):
                    e = best[name]
                    d = f"{name}-{e['version']}"
                    add_tar_file(tf, d, None)
                    add_tar_file(tf, f"{d}/desc", e["fragment"]["desc"].encode())
                    if with_files:
                        add_tar_file(tf, f"{d}/files", e["fragment"]["files"].encode())
            gz = common.gzip_bytes(buf.getvalue())
            adir = out / "arch" / arch
            write(adir / f"{repo}.{kind}.tar.gz", gz)
            write(adir / f"{repo}.{kind}", gz)  # plain copies, no symlinks over FTP
        chosen += [(arch, n, best[n]["version"]) for n in sorted(best)]
    return chosen


# --------------------------------------------------------------------------- apk
def apk_meta(out: Path, entries: list[dict], distros: dict) -> None:
    branch = distros["alpine"]["branch"]
    for arch in distros["alpine"]["architectures"]:
        sel = sorted((e for e in entries if e["arch"] in (arch, "noarch")),
                     key=lambda e: (e["pkgname"], e["version"]))
        text = "".join(e["fragment"]["stanza"].strip("\n") + "\n\n" for e in sel)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
            add_tar_file(tf, "APKINDEX", text.encode())
            add_tar_file(tf, "DESCRIPTION", b"lan.party repository\n")
        write(out / "alpine" / branch / arch / "APKINDEX.tar.gz", common.gzip_bytes(buf.getvalue()))


# --------------------------------------------------------------------------- pool + plan
def place_pool(out: Path, items: list[dict], index: dict) -> list[str]:
    uploads: list[str] = []
    for it in items:
        if it["action"] not in ("add", "replace"):
            continue
        e = index.get((it["fmt"], it["tool"], it["asset"]))
        if e is None:
            die(f"{it['asset']} is in the plan but not in index/: run fragment.py first")
        src = DL / it["fmt"] / it["tool"] / it["asset"]
        for pp in e["pool_paths"]:
            s = src.with_name(src.name + ".sig") if pp.endswith(".sig") else src
            if not s.exists():
                die(f"{s} missing (needed for {pp})")
            dest = out / pp
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, dest)
            uploads.append(pp)
    return sorted(set(uploads))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="public")
    ap.add_argument("--plan", default=str(common.WORK / "plan.json"))
    args = ap.parse_args()
    out = Path(args.out)
    distros = common.load_distros()
    index = common.load_index()
    by_fmt = {f: [e for (ff, _t, _a), e in index.items() if ff == f] for f in common.FORMATS}
    log("index: " + ", ".join(f"{f}={len(v)}" for f, v in by_fmt.items()))

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    deb_meta(out, by_fmt["deb"], distros)
    rpm_meta(out, by_fmt["rpm"], distros)
    chosen = arch_meta(out, by_fmt["arch"], distros)
    apk_meta(out, by_fmt["apk"], distros)
    for a, n, v in chosen:
        log(f"arch/{a}: {n} {v}")

    plan = common.read_json(Path(args.plan)) if Path(args.plan).exists() else {"items": []}
    upload = place_pool(out, plan["items"], index)
    live = {pp for e in index.values() for pp in e["pool_paths"]}
    delete = sorted({pp for it in plan["items"] if it["action"] == "remove"
                     for pp in it.get("pool_paths", [])} - live)
    pool_files = set(upload)
    metadata = sorted(str(p.relative_to(out)) for p in out.rglob("*")
                      if p.is_file() and str(p.relative_to(out)) not in pool_files)
    common.write_json(out / "deploy-plan.json",
                      {"upload": upload, "delete": delete, "metadata": metadata})
    log(f"deploy-plan: upload={len(upload)} delete={len(delete)} metadata={len(metadata)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
