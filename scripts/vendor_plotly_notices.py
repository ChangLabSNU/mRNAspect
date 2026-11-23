#!/usr/bin/env python3
"""Write the licence notices of the vendored plotly bundle and of everything inside it.

    python scripts/vendor_plotly_notices.py --work <an empty folder>

The report embeds ``mrnaspect/reporting/assets/plotly-cartesian-<version>.min.js``, and a
report is handed on as one file. plotly.js is MIT and carries other packages; several of
their licences (MIT, ISC, BSD) ask that the copyright notice and the terms travel with
every copy. The bundle keeps only a one-line mention of seven of them. This writes the
full texts to ``<bundle>.LICENSES.txt`` beside the bundle, which the wheel ships, the
report embeds and the app's notices include.

Run it when the bundle's version changes; the output is committed. It needs the network:

1. the published minified bundle, to check the vendored copy is that file, byte for byte;
2. the published unminified bundle of the same build, whose ``// node_modules/<package>``
   comments name every package bundled (the minified file has lost them);
3. plotly.js's ``package-lock.json`` and ``LICENSE`` at the version's tag, for the version
   of each package and plotly's own terms;
4. each package at that version from the npm registry's mirror, for its licence file.

What is downloaded is read as bytes or JSON and never run. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "mrnaspect" / "reporting" / "assets"
CDN = "https://cdn.jsdelivr.net/npm"
LISTING = "https://data.jsdelivr.com/v1/packages/npm"
RAW = "https://raw.githubusercontent.com/plotly/plotly.js"
RULE = "-" * 78

#: A licence file at a package's root, by name.
_LICENCE_NAME = re.compile(r"^/(licen[sc]e|copying|notice)([.\-_][^/]*)?$", re.I)
#: The heading of a README's licence section, for a package that ships no licence file.
_README_HEADING = re.compile(r"^(#+\s*licen[sc]e\b.*|licen[sc]e\s*\n[-=]+\s*)$", re.I | re.M)


def fetch(url: str, dest: Path) -> bytes:
    """``url``'s bytes, kept under ``dest`` so a second run asks for nothing twice."""
    if dest.is_file():
        return dest.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "licence-notice-collector"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def bundled_packages(unminified: str) -> dict[str, str]:
    """Package name -> its ``node_modules`` path, from the bundle's own module comments.

    A package can sit under another's ``node_modules`` at another version, so the path
    is kept: it is the key of the lock file. A name bundled from two paths is refused.
    """
    found: dict[str, str] = {}
    for path in re.findall(r"^\s*// (node_modules/\S+)", unminified, re.M):
        parts = path.split("/")
        last = len(parts) - 1 - parts[::-1].index("node_modules")
        width = 2 if parts[last + 1].startswith("@") else 1
        name = "/".join(parts[last + 1:last + 1 + width])
        where = "/".join(parts[:last + 1 + width])
        if found.setdefault(name, where) != where:
            raise SystemExit(f"{name} is bundled from two places: {found[name]} and {where}")
    return found


def carried_notices(minified: str) -> dict[str, list[tuple[str, str]]]:
    """Package name -> the (file, comment) notices the minified bundle itself carries.

    The bundler keeps the comments its packages mark for keeping, in one block at the
    end, each under the file it came from. They are short, and for some packages the
    only place a copyright line is written at all.
    """
    block = re.search(r"/\*! Bundled license information:\n(.*?)\n\*/", minified, re.S)
    found: dict[str, list[tuple[str, str]]] = {}
    if not block:
        return found
    for file, body in re.findall(r"^(\S+\.js):\n((?:(?:  .*)?\n)+?)(?=\S|\Z)",
                                 block.group(1) + "\n", re.M):
        parts = file.split("/")
        name = "/".join(parts[:2]) if file.startswith("@") else parts[0]
        text = "\n".join(line[2:] if line.startswith("  ") else line
                         for line in body.rstrip("\n").split("\n"))
        found.setdefault(name, []).append((file, text.strip("\n") + "\n"))
    return found


def readme_licence(readme: str) -> str:
    """The licence section of a README, or '' when it has none."""
    match = _README_HEADING.search(readme)
    if not match:
        return ""
    rest = readme[match.end():]
    level = len(match.group(0)) - len(match.group(0).lstrip("#"))
    stop = re.search(r"^#{1,%d}\s" % max(level, 1), rest, re.M) if level else \
        re.search(r"^.+\n[-=]{3,}\s*$", rest, re.M)
    return (match.group(0) + rest[:stop.start() if stop else len(rest)]).strip() + "\n"


def package_notice(name: str, version: str,
                   work: Path) -> tuple[list[tuple[str, str]], str, dict]:
    """The licence texts a package ships, as (source URL, text); the licence its
    ``package.json`` states; and that file's contents."""
    base = f"{CDN}/{name}@{version}"
    cache = work / "packages" / name.replace("/", "__") / version
    listing = json.loads(fetch(f"{LISTING}/{name}@{version}?structure=flat",
                               cache / "listing.json"))
    names = sorted(f["name"] for f in listing.get("files", []))
    meta = json.loads(fetch(f"{base}/package.json", cache / "package.json"))
    stated = meta.get("license") or meta.get("licenses") or "not stated"
    if not isinstance(stated, str):
        stated = json.dumps(stated, sort_keys=True)
    texts = []
    for file in names:
        if _LICENCE_NAME.match(file):
            body = fetch(base + file, cache / file.lstrip("/")).decode("utf-8", "replace")
            texts.append((base + file, body.replace("\r\n", "\n").strip() + "\n"))
    if not texts:
        for file in names:
            if re.fullmatch(r"/readme(\.[a-z]+)?", file, re.I):
                body = fetch(base + file, cache / file.lstrip("/")).decode("utf-8", "replace")
                section = readme_licence(body.replace("\r\n", "\n"))
                if section:
                    texts.append((f"{base}{file} (its licence section; the package ships "
                                  "no licence file)", section))
    return texts, stated, meta


def stated_only(name: str, version: str, meta: dict) -> str:
    """For a package that ships no licence text: what its ``package.json`` itself says.

    No text is written for it here. A licence's wording and its copyright line are the
    author's to give, and a package that gives neither is described by what it does give.
    """
    lines = ["Licence text: the package ships none, and no copyright line. Its "
             f"package.json ({CDN}/{name}@{version}/package.json) states:"]
    for key in ("license", "author", "contributors", "repository"):
        if meta.get(key):
            lines.append(f"  {json.dumps(key)}: {json.dumps(meta[key], sort_keys=True)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", type=Path, required=True,
                        help="a folder for the downloads (kept, so a rerun is offline)")
    parser.add_argument("--bundle", type=Path, default=None,
                        help="the vendored bundle (default: the one under reporting/assets)")
    args = parser.parse_args(argv)
    bundles = [args.bundle] if args.bundle else sorted(ASSETS.glob("plotly-cartesian-*.min.js"))
    if len(bundles) != 1:
        raise SystemExit(f"expected one vendored plotly bundle, found {len(bundles)}")
    bundle = bundles[0]
    version = re.fullmatch(r"plotly-cartesian-(\d+\.\d+\.\d+)\.min\.js", bundle.name)
    if not version:
        raise SystemExit(f"cannot read a version from {bundle.name}")
    v = version.group(1)
    work = args.work
    try:
        published = fetch(f"{CDN}/plotly.js-cartesian-dist-min@{v}/plotly-cartesian.min.js",
                          work / "plotly-cartesian.min.js")
        unminified = fetch(f"{CDN}/plotly.js-cartesian-dist@{v}/plotly-cartesian.js",
                           work / "plotly-cartesian.js").decode("utf-8", "replace")
        lock = json.loads(fetch(f"{RAW}/v{v}/package-lock.json", work / "package-lock.json"))
        own = fetch(f"{RAW}/v{v}/LICENSE", work / "LICENSE").decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"could not download plotly.js {v}'s files: {exc}")
    vendored = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if hashlib.sha256(published).hexdigest() != vendored:
        raise SystemExit(f"{bundle.name} is not the published plotly.js-cartesian-dist-min "
                         f"{v}: the notices of that build would not describe it")

    packages = bundled_packages(unminified)
    # Every package the minified bundle itself names must be among them.
    carried = carried_notices(bundle.read_text("utf-8"))
    if not carried or set(carried) - set(packages):
        raise SystemExit(f"the bundle's own notices are missing, or name packages its "
                         f"unminified form does not: {sorted(set(carried) - set(packages))}")

    sections, missing, no_text = [], [], []
    for name in sorted(packages, key=str.lower):
        entry = lock.get("packages", {}).get(packages[name])
        if not entry or not entry.get("version"):
            raise SystemExit(f"{packages[name]} is not in plotly.js {v}'s package-lock.json")
        try:
            texts, stated, meta = package_notice(name, entry["version"], work)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SystemExit(f"could not read {name} {entry['version']}: {exc}")
        head = [RULE, f"{name} {entry['version']}", RULE, f"Licence: {stated}"]
        if not texts:
            head.append(stated_only(name, entry["version"], meta))
            (no_text if meta.get("license") else missing).append(
                f"{name} {entry['version']}")
        texts += [(f"the notice {bundle.name} itself carries, from {file}", text)
                  for file, text in carried.get(name, [])]
        body = "".join(f"\n--- {source} ---\n{text}" for source, text in texts)
        sections.append("\n".join(head) + "\n" + body)

    out = bundle.with_name(bundle.name.removesuffix(".min.js") + ".LICENSES.txt")
    header = f"""Licence notices for {bundle.name}
{'=' * (20 + len(bundle.name))}

This file belongs with {bundle.name} (sha256 {vendored}),
which is plotly.js-cartesian-dist-min {v} as published, unchanged. It gives the terms of
plotly.js itself and of the {len(packages)} packages that build carries, each at the version
plotly.js {v} was built with (its package-lock.json), with the licence text that package
ships. The list of packages is read from the unminified form of the same build, which
names the source of every module in it.

Written by scripts/vendor_plotly_notices.py, not by hand.

{RULE}
plotly.js {v}
{RULE}
Licence: MIT

--- {RAW}/v{v}/LICENSE ---
{own.strip()}
"""
    out.write_text(header + "\n" + "\n".join(sections), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}: plotly.js {v} and {len(packages)} packages, "
          f"{out.stat().st_size:,} bytes")
    if no_text:
        print("ships no licence text, described by its package.json: " + ", ".join(no_text))
    if missing:
        print("neither a licence text nor a stated licence: " + ", ".join(missing),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
