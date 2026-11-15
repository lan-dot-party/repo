#!/usr/bin/env python3
"""fragment.py <plan.json>: write/replace/delete index/<fmt>/<tool>/<asset>.json (pipeline step 4)."""
from __future__ import annotations

import argparse
import base64
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
import gzip

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import die, log  # noqa: E402

DL = common.WORK / "dl"


def require(binary: str, why: str) -> None:
    if shutil.which(binary) is None:
        die(f"'{binary}' is not installed but needed for {why}. "
            f"Install it or restrict the run with --only-fmt on sync.py plan.")


def run(cmd: list[str], **kw) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        die(f"{' '.join(cmd)} failed:\n{r.stderr}")
    return r.stdout


def targets_for(tool: dict, fmt: str, asset: str, distros: dict) -> list[str]:
    """Suites (deb) or repo paths (rpm) this asset belongs to."""
    if fmt == "deb":
        cands = [(s["name"], s["tags"]) for s in distros["deb"]["suite"]]
    elif fmt == "rpm":
        cands = [(r["path"], r["tags"]) for r in distros["rpm"]["repo"]]
    else:
        return []
    if tool["build"] == "generic":
        return [n for n, _ in cands]
    m = re.search(tool[fmt], asset)
    distro = m.groupdict().get("distro") if m else None
    if not distro:
        die(f"{asset}: per_distro tool but regex has no 'distro' group match")
    out = [n for n, rx in cands if re.search(rx, distro)]
    if not out:
        die(f"{asset}: distro tag '{distro}' matches no entry in distros.toml")
    return out


# --------------------------------------------------------------------------- deb
def frag_deb(src: Path, it: dict, tool: dict, distros: dict) -> dict:
    require("apt-ftparchive", "deb fragments (apt-utils)")
    out = run(["apt-ftparchive", "packages", src.name], cwd=src.parent).strip()
    stanza = re.sub(r"(?m)^Filename: .*$", f"Filename: pool/{it['tool']}/{it['asset']}", out)
    fields = dict(re.findall(r"(?m)^([A-Za-z0-9-]+): (.*)$", stanza))
    if "Package" not in fields or "Architecture" not in fields:
        die(f"{src.name}: unexpected apt-ftparchive output")
    return {
        "arch": fields["Architecture"], "pkgname": fields["Package"],
        "version": fields["Version"],
        "pool_paths": [f"deb/pool/{it['tool']}/{it['asset']}"],
        "targets": targets_for(tool, "deb", it["asset"], distros),
        "fragment": {"stanza": stanza},
    }


# --------------------------------------------------------------------------- rpm
def _decompress(p: Path) -> bytes:
    data = p.read_bytes()
    if p.suffix == ".gz":
        return gzip.decompress(data)
    if p.suffix == ".xz":
        import lzma
        return lzma.decompress(data)
    if p.suffix == ".zst":
        return subprocess.run(["zstd", "-dc", str(p)], check=True, capture_output=True).stdout
    if p.suffix == ".bz2":
        import bz2
        return bz2.decompress(data)
    return data


def frag_rpm(src: Path, it: dict, tool: dict, distros: dict) -> dict:
    require("createrepo_c", "rpm fragments (createrepo-c)")
    with tempfile.TemporaryDirectory() as td:
        shutil.copy2(src, td)
        run(["createrepo_c", "--quiet", "--simple-md-filenames", "--no-database", td])
        texts = {}
        for kind in ("primary", "filelists", "other"):
            # createrepo_c >= 1.0 defaults to zstd; older versions to gzip -> accept all
            found = sorted((Path(td) / "repodata").glob(f"{kind}.xml*"))
            if not found:
                die(f"createrepo_c wrote no {kind}.xml* for {src.name}")
            texts[kind] = _decompress(found[0]).decode("utf-8")
    frag = {}
    for kind, text in texts.items():
        m = re.search(r'<package\b.*?</package>', text, re.S)
        if not m:
            die(f"createrepo_c produced no <package> in {kind}.xml for {src.name}")
        frag[kind] = m.group(0)
    pool_base = f"{common.base_url()}/rpm/pool/{it['tool']}/"
    loc = re.search(r'<location [^>]*/>', frag["primary"])
    if not loc:
        die(f"no <location> in primary for {src.name}")
    esc = lambda s: s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")  # noqa: E731
    frag["primary"] = frag["primary"].replace(
        loc.group(0), f'<location xml:base="{esc(pool_base)}" href="{esc(it["asset"])}"/>')
    # sanity: well-formed with the namespaces the assembler will declare
    p = ET.fromstring('<m xmlns="http://linux.duke.edu/metadata/common" '
                      'xmlns:rpm="http://linux.duke.edu/metadata/rpm">' + frag["primary"] + "</m>")
    ns = {"c": "http://linux.duke.edu/metadata/common"}
    pkg = p.find("c:package", ns)
    ver = pkg.find("c:version", ns)
    epoch = ver.get("epoch", "0")
    version = f"{ver.get('ver')}-{ver.get('rel')}"
    if epoch not in ("", "0"):
        version = f"{epoch}:{version}"
    return {
        "arch": pkg.find("c:arch", ns).text, "pkgname": pkg.find("c:name", ns).text,
        "version": version,
        "pool_paths": [f"rpm/pool/{it['tool']}/{it['asset']}"],
        "targets": targets_for(tool, "rpm", it["asset"], distros),
        "fragment": frag,
    }


# --------------------------------------------------------------------------- arch
ARCH_MULTI = {  # PKGINFO key -> desc key
    "group": "GROUPS", "license": "LICENSE", "replaces": "REPLACES", "conflict": "CONFLICTS",
    "provides": "PROVIDES", "depend": "DEPENDS", "optdepend": "OPTDEPENDS",
    "makedepend": "MAKEDEPENDS", "checkdepend": "CHECKDEPENDS", "backup": "BACKUP",
}


def read_pkg_tar(src: Path) -> tuple[dict[str, list[str]], list[str], int]:
    """Return (.PKGINFO multi-dict, file list without dot-files, uncompressed tar size)."""
    require("zstd", "arch fragments")
    proc = subprocess.Popen(["zstd", "-dc", str(src)], stdout=subprocess.PIPE)
    info: dict[str, list[str]] = {}
    files: list[str] = []
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
        for m in tf:
            if m.name == ".PKGINFO":
                for line in tf.extractfile(m).read().decode("utf-8").splitlines():
                    if line.startswith("#") or " = " not in line:
                        continue
                    k, v = line.split(" = ", 1)
                    info.setdefault(k.strip(), []).append(v.strip())
            elif not m.name.startswith("."):
                files.append(m.name.removeprefix("./") + ("/" if m.isdir() else ""))
        while proc.stdout.read(1 << 20):
            pass
    if proc.wait():
        die(f"zstd -dc failed for {src}")
    if not info:
        die(f"{src.name}: no .PKGINFO")
    return info, files, 0


def frag_arch(src: Path, it: dict, tool: dict, distros: dict) -> dict:
    info, files, _ = read_pkg_tar(src)
    sig_path = src.with_name(src.name + ".sig")
    if not sig_path.exists():
        die(f"{sig_path} missing: run scripts/sign-packages.sh first")
    sig = base64.b64encode(sig_path.read_bytes()).decode()
    data = src.read_bytes()
    one = lambda k: info.get(k, [""])[0]  # noqa: E731
    name, pkgver = one("pkgname"), one("pkgver")
    sections: list[tuple[str, list[str]]] = [
        ("FILENAME", [it["asset"]]), ("NAME", [name]), ("BASE", [one("pkgbase") or name]),
        ("VERSION", [pkgver]), ("DESC", [one("pkgdesc")]), ("GROUPS", info.get("group", [])),
        ("CSIZE", [str(len(data))]), ("ISIZE", [one("size")]),
        ("MD5SUM", [hashlib.md5(data).hexdigest()]),
        ("SHA256SUM", [hashlib.sha256(data).hexdigest()]), ("PGPSIG", [sig]),
        ("URL", [one("url")]), ("LICENSE", info.get("license", [])), ("ARCH", [one("arch")]),
        ("BUILDDATE", [one("builddate")]), ("PACKAGER", [one("packager")]),
        ("REPLACES", info.get("replaces", [])), ("CONFLICTS", info.get("conflict", [])),
        ("PROVIDES", info.get("provides", [])), ("DEPENDS", info.get("depend", [])),
        ("OPTDEPENDS", info.get("optdepend", [])), ("MAKEDEPENDS", info.get("makedepend", [])),
        ("CHECKDEPENDS", info.get("checkdepend", [])), ("BACKUP", info.get("backup", [])),
    ]
    desc = "".join(f"%{k}%\n" + "\n".join(v) + "\n\n" for k, v in sections if any(v))
    arch = one("arch")
    archs = distros["arch"]["architectures"]
    dirs = archs if arch == "any" else [arch]
    pools = []
    for a in dirs:
        pools += [f"arch/{a}/{it['asset']}", f"arch/{a}/{it['asset']}.sig"]
    return {
        "arch": arch, "pkgname": name, "version": pkgver, "pool_paths": pools, "targets": [],
        "fragment": {"desc": desc, "files": "%FILES%\n" + "\n".join(files) + "\n",
                     "name": name, "pkgver": pkgver},
    }


# --------------------------------------------------------------------------- apk
def frag_apk_batch(batch: list[tuple[Path, dict, dict]], distros: dict) -> list[dict]:
    require("docker", "apk fragments (alpine container)")
    import os
    branch = distros["alpine"]["branch"]
    results = []
    with tempfile.TemporaryDirectory(dir=common.WORK) as td:
        td_p = Path(td)
        (td_p / "in").mkdir()
        (td_p / "out").mkdir()
        for n, (src, it, _tool) in enumerate(batch):
            shutil.copy2(src, td_p / "in" / f"{n}.apk")
        script = ('set -e; for f in /w/in/*.apk; do b=$(basename "$f" .apk); '
                  'apk index --allow-untrusted -o /w/out/$b.tar.gz "$f"; done')
        run(["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
             "-v", f"{td}:/w", "alpine:3.21", "sh", "-c", script])
        for n, (src, it, _tool) in enumerate(batch):
            with tarfile.open(td_p / "out" / f"{n}.tar.gz") as tf:
                text = tf.extractfile("APKINDEX").read().decode("utf-8")
            stanza = text.strip("\n")
            f = dict(re.findall(r"(?m)^([A-Za-z]):(.*)$", stanza))
            arch, pkg, ver = f["A"].strip(), f["P"].strip(), f["V"].strip()
            norm = f"{pkg}-{ver}.apk"
            pools = [f"alpine/{branch}/{a}/{norm}"
                     for a in (distros["alpine"]["architectures"] if arch == "noarch" else [arch])]
            results.append({
                "arch": arch, "pkgname": pkg, "version": ver, "pool_paths": pools, "targets": [],
                "fragment": {"stanza": stanza, "filename": norm},
            })
    return results


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plan", nargs="?", default=str(common.WORK / "plan.json"))
    args = ap.parse_args()
    plan = common.read_json(Path(args.plan))
    tools = {t["name"]: t for t in common.load_tools()}
    distros = common.load_distros()

    apk_batch: list[tuple[Path, dict, dict]] = []
    n_write = n_del = 0
    for it in plan["items"]:
        ipath = common.index_path(it["fmt"], it["tool"], it["asset"])
        if it["action"] == "remove":
            if ipath.exists():
                ipath.unlink()
                n_del += 1
            continue
        if it["action"] not in ("add", "replace"):
            continue
        tool = tools[it["tool"]]
        src = DL / it["fmt"] / it["tool"] / it["asset"]
        if not src.exists():
            die(f"{src} missing: run sync.py download first")
        if it["fmt"] == "apk":
            apk_batch.append((src, it, tool))
            continue
        fn = {"deb": frag_deb, "rpm": frag_rpm, "arch": frag_arch}[it["fmt"]]
        extra = fn(src, it, tool, distros)
        write_entry(ipath, it, src, extra)
        n_write += 1
    if apk_batch:
        for (src, it, _t), extra in zip(apk_batch, frag_apk_batch(apk_batch, distros)):
            write_entry(common.index_path(it["fmt"], it["tool"], it["asset"]), it, src, extra)
            n_write += 1
    log(f"fragments: {n_write} written, {n_del} removed")
    return 0


def write_entry(ipath: Path, it: dict, src: Path, extra: dict) -> None:
    entry = {
        "fmt": it["fmt"], "tool": it["tool"], "tag": it["tag"], "asset": it["asset"],
        "upstream_sha256": it["upstream_sha256"] or common.sha256_file(src),
        "sha256": common.sha256_file(src), "size": src.stat().st_size,
    }
    entry.update(extra)
    common.write_json(ipath, entry)


if __name__ == "__main__":
    sys.exit(main())
