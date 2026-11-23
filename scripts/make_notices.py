#!/usr/bin/env python3
"""Write the notices for the third parties' software a staged environment carries.

WHY THIS EXISTS. The app redistributes about 120 conda packages, the Rust crates of its
window and the JavaScript of its page. MIT, BSD, Apache and the LGPL each ask that their
copyright notice and text go with every copy, and "each under its own licence" in About
is not that (2026-11-12).

The file is GENERATED from what the staged environment itself records, at every staging,
so it cannot describe a different solve than the one that ships:

    python3 scripts/make_notices.py <env> [--root <checkout>]    write <env>/THIRD-PARTY-NOTICES.txt
    python3 scripts/make_notices.py --check <env>                is the file there, and whole?

Where a licence text comes from, in this order: the package's own folder in the package
cache (`info/licenses/`); a licence file the package installs; another output of the same
recipe (freetype holds the texts for libfreetype6); a copy kept in `scripts/notices/`.

A package conda did not install has no record in `conda-meta`: one installed from a wheel
(WeasyPrint, from PyPI, since conda-forge had no fixed version). Those are read from their
`.dist-info` in site-packages, which names the package, its licence, and holds its text.
Core's own is not listed: this file is about other people's software.

What cannot be found is written into the file under INCOMPLETE and printed as a warning.
Writing still succeeds, so a developer's `tauri dev` is not stopped by it; `--check`, which
`check_bundle.sh` runs on the built .app before a release, fails on it.

Standard library only: it runs on the interpreter of the environment it describes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The file's name inside the environment. The shell opens it by this name (`lib.rs`).
NAME = "THIRD-PARTY-NOTICES.txt"

HERE = Path(__file__).resolve().parent
#: Texts for packages that carry none; each is accounted for in its README.
KEPT = HERE / "notices"
RULE = "-" * 80

#: Core's own distribution, which the file does not list.
OURS = "mrnaspect"

#: Where a licence offers a choice, the one taken, and what else a reader should know.
TAKEN: dict[str, tuple[str, str]] = {
    "freetype": ("FTL", "the FreeType licence, of the two offered"),
    "libfreetype": ("FTL", "the FreeType licence, of the two offered"),
    "libfreetype6": ("FTL", "the FreeType licence, of the two offered"),
    "libev": ("BSD-2-Clause", "of the two offered"),
    "pyphen": ("MPL-1.1", "one of the three its own LICENSE offers. Its hyphenation "
               "dictionaries, each under its own licence, are left out of this app"),
}

#: A licence that is another licence plus an exception: the kept copy of the licence it
#: modifies, added when the package's own texts hold only the exception.
MODIFIES: dict[str, tuple[str, str]] = {
    "GCC-exception-3.1": ("GNU GENERAL PUBLIC LICENSE", "GPL-3.0.txt"),
}

#: The header that carries FreeType's copyright years, for the credit the FTL asks for.
_FREETYPE_HEADER = "include/freetype2/freetype/freetype.h"
_LICENCE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|UNLICEN[CS]E)", re.I)
_INSTALLED = re.compile(
    r"(^|/)share/licenses/|\.dist-info/licenses/|\.dist-info/(LICEN[CS]E|COPYING|NOTICE)[^/]*$",
    re.I)


@dataclass
class Entry:
    """One third party: what is said about it, and its licence texts."""

    name: str
    version: str
    licence: str
    lines: list[str] = field(default_factory=list)
    texts: list[tuple[str, str]] = field(default_factory=list)


def _read(path: Path) -> str:
    """A licence file as text: whatever its encoding, with one newline at its end."""
    text = path.read_bytes().decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def _texts_under(folder: Path) -> list[tuple[str, str]]:
    """Every file under ``folder``, as (path inside it, text), in a fixed order."""
    if not folder.is_dir():
        return []
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    return [(str(p.relative_to(folder)), _read(p)) for p in files]


def _licence_files(folder: Path) -> list[tuple[str, str]]:
    """The licence files directly in a crate's or an npm package's folder."""
    if not folder.is_dir():
        return []
    return [(p.name, _read(p)) for p in sorted(folder.iterdir())
            if p.is_file() and _LICENCE_FILE.match(p.name)]


def _sources(cache: Path) -> list[str]:
    """Where the source of this build is, from the recipe the package carries.

    The recipe is read as lines, not parsed: the first ``source:`` block's addresses and
    checksums. Both recipe formats conda-forge uses keep them there.
    """
    for name in ("meta.yaml", "rendered_recipe.yaml", "recipe.yaml"):
        recipe = cache / "info" / "recipe" / name
        if not recipe.is_file():
            continue
        lines = recipe.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            start = re.match(r"^(\s*)source:\s*$", line)
            if not start:
                continue
            block = []
            for following in lines[i + 1:]:
                indent = len(following) - len(following.lstrip())
                if following.strip() and indent <= len(start.group(1)) \
                        and not following.lstrip().startswith("- "):
                    break
                if not following.lstrip().startswith("#"):
                    block.append(following)
            body = "\n".join(block)
            urls = list(dict.fromkeys(re.findall(r"https?://[^\s'\"]+", body)))
            sums = re.findall(r"sha256:\s*([0-9a-f]{64})", body)
            if urls:
                if len(urls) == len(sums):
                    return [f"{u} (sha256 {s})" for u, s in zip(urls, sums)]
                return urls + [f"sha256 {s}" for s in sums]
    return []


def _freetype_credit(env: Path, texts: list[tuple[str, str]]) -> tuple[str, str]:
    """The acknowledgement the FTL asks for, and what is missing to write it ("" if none).

    The sentence is the licence's own, and its year is "the value from the FreeType version
    you actually use": the last year of the copyright line in the header that ships.
    """
    sentence = next((m.group(0) for _, text in texts for m in [re.search(
        r"Portions of this software are copyright[\s\S]*?All rights reserved\.", text)] if m), "")
    if not sentence:
        return "", ""
    header = env / _FREETYPE_HEADER
    year = re.search(r"Copyright \(C\) \d{4}-(\d{4})", header.read_text(errors="replace")) \
        if header.is_file() else None
    if not year:
        return "", f"the year for FreeType's credit (it is read from {_FREETYPE_HEADER})"
    return " ".join(sentence.split()).replace("<year>", year.group(1)), ""


def _plain(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_installs(env: Path) -> list[dict]:
    """The packages in ``env`` that conda did not install, from their ``.dist-info``.

    conda marks the Python packages it installs (``INSTALLER`` says ``conda``); anything
    else came from a wheel. Left out: Core's own, and a ``.dist-info`` whose package is no
    longer there (a record left behind, with none of the files it lists).

    Returns:
        One dict per package: ``name``, ``version``, ``licence``, ``home``, ``info``.
    """
    found = []
    for info in sorted(env.glob("lib/python3*/site-packages/*.dist-info")):
        installer = info / "INSTALLER"
        if installer.is_file() and installer.read_text().strip() == "conda":
            continue
        fields: dict[str, list[str]] = {}
        try:
            head = (info / "METADATA").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in head.split("\n\n", 1)[0].splitlines():
            key, sep, value = line.partition(": ")
            if sep and not line.startswith((" ", "\t")):
                fields.setdefault(key, []).append(value.strip())
        name, version = fields.get("Name", [""])[0], fields.get("Version", [""])[0]
        if not name or not version or _plain(name) == OURS:
            continue
        record = info / "RECORD"
        if record.is_file():
            listed = [line.split(",")[0] for line in record.read_text().splitlines() if line]
            # Compiled files do not count: a .pyc outlives the module it was made from.
            if not any((info.parent / f).is_file() for f in listed
                       if not f.startswith(info.name + "/") and not f.endswith(".pyc")):
                continue
        classifiers = [c.split(" :: ")[-1] for c in fields.get("Classifier", [])
                       if c.startswith("License ::")]
        licence = (fields.get("License-Expression") or classifiers
                   or [v for v in fields.get("License", []) if len(v) < 80] or ["not stated"])[0]
        home = next((u.split(", ", 1)[1] for u in fields.get("Project-URL", [])
                     if u.lower().startswith("homepage, ")), fields.get("Home-page", [""])[0])
        found.append({"name": name, "version": version, "licence": licence, "home": home,
                      "info": info})
    return found


def _wheel_entry(env: Path, package: dict) -> tuple[Entry, list[str]]:
    """The notice of one wheel-installed package, and what could not be found for it."""
    info: Path = package["info"]
    entry = Entry(package["name"], package["version"], package["licence"])
    entry.lines += [f"Licence: {entry.licence}",
                    "Installed from: its wheel (PyPI); conda has no record of it"]
    if package["home"]:
        entry.lines.append(f"Home: {package['home']}")
    texts = _texts_under(info / "licenses") if (info / "licenses").is_dir() else []
    texts += [(p.name, _read(p)) for p in sorted(info.iterdir())
              if p.is_file() and re.match(r"(?i)(licen[sc]e|copying|notice)", p.name)]
    if texts:
        entry.texts = texts
        entry.lines.append(f"Licence text from: {info.relative_to(env).as_posix()}")
        return entry, []
    entry.lines.append("Licence text: NOT FOUND.")
    return entry, [f"no licence text was found for {entry.name} {entry.version} "
                   f"(looked in {info.relative_to(env).as_posix()})"]


def collect_env(env: Path) -> tuple[list[Entry], list[str]]:
    """The packages in ``env``: conda's, by its records, and those installed from a wheel.

    Returns:
        The entries, by name, and what could not be found (empty when nothing is missing).
    """
    records = []
    for path in sorted((env / "conda-meta").glob("*.json")):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and data.get("name") and data.get("version"):
            records.append(data)
    records.sort(key=lambda d: (d["name"].lower(), d["version"]))

    found: dict[str, dict] = {}
    for data in records:
        cache = Path(data.get("extracted_package_dir") or "/nonexistent")
        about_file = cache / "info" / "about.json"
        about = json.loads(about_file.read_text()) if about_file.is_file() else {}
        extra = about.get("extra") or {}
        found[data["name"]] = {
            "cache": cache, "about": about,
            "recipe": (extra.get("remote_url"), extra.get("sha")),
            "own": _texts_under(cache / "info" / "licenses"),
            "installed": [f for f in data.get("files", [])
                          if _INSTALLED.search(f) and (env / f).is_file()],
        }

    entries, missing = [], []
    for data in records:
        name, version = data["name"], data["version"]
        info = found[name]
        entry = Entry(name, version, data.get("license") or "not stated")
        entry.lines.append(f"Licence: {entry.licence}")
        if name in TAKEN:
            entry.lines.append(f"Taken: {TAKEN[name][0]}, {TAKEN[name][1]}.")
        if data.get("url"):
            entry.lines.append(f"Package: {data['url']}")
        if info["about"].get("home"):
            entry.lines.append(f"Home: {info['about']['home']}")
        if all(info["recipe"]):
            entry.lines.append(f"Recipe: {info['recipe'][0]} at {info['recipe'][1]}")
        entry.lines += [f"Source of this build: {s}" for s in _sources(info["cache"])]

        sibling = next((other for other, o in found.items() if other != name and o["own"]
                        and all(info["recipe"]) and o["recipe"] == info["recipe"]), None)
        kept = KEPT / f"{name}.txt"
        if info["own"]:
            entry.texts, origin = info["own"], "its package"
        elif info["installed"]:
            entry.texts = [(f, _read(env / f)) for f in info["installed"]]
            origin = "the files it installs"
        elif not data.get("files"):
            origin = ""
            entry.lines.append("This package installs no file of its own.")
        elif sibling:
            entry.texts = found[sibling]["own"]
            origin = (f"{sibling} {next(d['version'] for d in records if d['name'] == sibling)}, "
                      "built from the same recipe")
        elif kept.is_file():
            entry.texts, origin = [(kept.name, _read(kept))], f"scripts/notices/{kept.name}"
        else:
            origin = ""
            missing.append(f"no licence text was found for {name} {version} (looked in "
                           f"{info['cache'] / 'info' / 'licenses'}, the files it installs, "
                           f"its recipe's other packages and scripts/notices/)")
            entry.lines.append("Licence text: NOT FOUND.")
        if origin:
            entry.lines.append(f"Licence text from: {origin}")

        for marker, (heading, kept_name) in MODIFIES.items():
            if marker in entry.licence and entry.texts \
                    and not any(heading in text for _, text in entry.texts):
                entry.texts = entry.texts + [
                    (f"{kept_name} (what the exception modifies; scripts/notices/)",
                     _read(KEPT / kept_name))]

        if TAKEN.get(name, ("",))[0] == "FTL":
            credit, lacking = _freetype_credit(env, entry.texts)
            if credit:
                entry.lines.append(f"Credit the FreeType licence asks for: {credit}")
            if lacking:
                missing.append(lacking)
        entries.append(entry)
    for package in wheel_installs(env):
        entry, lacking = _wheel_entry(env, package)
        entries.append(entry)
        missing += lacking
    entries.sort(key=lambda e: (e.name.lower(), e.version))
    return entries, missing


def collect_crates(root: Path) -> tuple[list[Entry], list[str]]:
    """The Rust crates the window is built from, by `cargo metadata`.

    Followed from the shell's own package through what it links and what its build runs;
    a crate only its tests use is not in the app. Needs cargo, so a real answer comes
    from the machine that builds the app.
    """
    cargo = shutil.which("cargo")
    if not cargo:
        return [], ["the window's Rust crates are not listed: cargo is not on this machine, "
                    "and their notices are written where the app is built"]
    command = [cargo, "metadata", "--format-version", "1", "--locked",
               "--manifest-path", str(root / "gui" / "src-tauri" / "Cargo.toml")]
    rustc = shutil.which("rustc")
    if rustc:
        told = subprocess.run([rustc, "-vV"], capture_output=True, text=True)
        host = re.search(r"^host: (\S+)", told.stdout, re.M)
        if host:
            command += ["--filter-platform", host.group(1)]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        why = (proc.stderr.strip().splitlines() or ["no message"])[-1]
        return [], [f"the window's Rust crates are not listed: cargo metadata failed ({why})"]
    meta = json.loads(proc.stdout)

    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in (meta.get("resolve") or {}).get("nodes", [])}
    resolve = meta.get("resolve") or {}
    roots = [resolve["root"]] if resolve.get("root") else list(meta.get("workspace_members", []))
    seen, queue = set(roots), list(roots)
    while queue:
        for dep in nodes.get(queue.pop(), {}).get("deps", []):
            kinds = [k.get("kind") for k in dep.get("dep_kinds", [{"kind": None}])]
            if dep["pkg"] not in seen and any(k in (None, "build") for k in kinds):
                seen.add(dep["pkg"])
                queue.append(dep["pkg"])

    entries = []
    for ident in sorted(seen - set(roots),
                        key=lambda i: (packages[i]["name"].lower(), packages[i]["version"])):
        package = packages[ident]
        entry = Entry(package["name"], package["version"],
                      package.get("license") or "see its licence file")
        entry.lines.append(f"Licence: {entry.licence}")
        if package.get("repository"):
            entry.lines.append(f"Repository: {package['repository']}")
        if package.get("authors"):
            entry.lines.append("Authors: " + "; ".join(package["authors"][:5]))
        entry.texts = _licence_files(Path(package["manifest_path"]).parent)
        if not entry.texts:
            entry.lines.append("The published crate holds no licence file; the licence above "
                               "is what it declares.")
        entries.append(entry)
    return entries, []


def collect_page(root: Path) -> tuple[list[Entry], list[str]]:
    """The JavaScript the page loads: its npm packages, and plotly's bundle.

    Development dependencies (vite, TypeScript, the Tauri CLI) build the page and are not
    in it. plotly is one file under `gui/public/`: its own header, then the licence texts
    of plotly.js and of every package inside the bundle, from the file kept beside the
    copy the report embeds (scripts/vendor_plotly_notices.py). The bundle's own mentions
    of what it carries are one line each and name no copyright holder for some.
    """
    gui = root / "gui"
    entries, missing = [], []
    lock = gui / "package-lock.json"
    if lock.is_file():
        for path, info in sorted(json.loads(lock.read_text()).get("packages", {}).items()):
            if not path or info.get("dev"):
                continue
            name = path.rsplit("node_modules/", 1)[-1]
            entry = Entry(name, info.get("version", "?"), info.get("license") or "not stated")
            entry.lines.append(f"Licence: {entry.licence}")
            if info.get("resolved"):
                entry.lines.append(f"Package: {info['resolved']}")
            entry.texts = _licence_files(gui / path)
            entries.append(entry)

        # A package may publish a statement of its licences (LICENSE.spdx) and no text:
        # @tauri-apps/plugin-dialog does. The texts are then the ones its publisher ships
        # with another package of the same scope under the same licences.
        def real(entry: Entry) -> list[tuple[str, str]]:
            return [t for t in entry.texts if not t[0].lower().endswith(".spdx")]

        def ids(entry: Entry) -> set[str]:
            return set(re.split(r"\s+OR\s+", entry.licence))

        for entry in entries:
            if real(entry):
                continue
            scope = entry.name.split("/")[0]
            donor = next((o for o in entries if o is not entry and real(o) and scope.startswith("@")
                          and o.name.split("/")[0] == scope and ids(o) == ids(entry)), None)
            if donor:
                entry.texts = entry.texts + real(donor)
                entry.lines.append(f"Licence text from: {donor.name} {donor.version}, of the "
                                   "same publisher and under the same licences")
            else:
                missing.append(f"the page's {entry.name} {entry.version} has no licence text "
                               f"under gui/node_modules/{entry.name} (run `npm ci` in gui/ "
                               "before staging)")
                entry.lines.append("Licence text: NOT FOUND.")
    else:
        missing.append("the page's npm packages are not listed: gui/package-lock.json is not there")

    bundles = sorted((gui / "public").glob("plotly-*.min.js")) if (gui / "public").is_dir() else []
    if not bundles:
        missing.append("plotly's bundle is not under gui/public/")
    for bundle in bundles:
        source = bundle.read_text(encoding="utf-8", errors="replace")
        header = re.match(r"\s*/\*\*[\s\S]*?\*/", source)
        version = re.search(r"v(\d+\.\d+\.\d+)", header.group(0)) if header else None
        kept = root / "mrnaspect" / "reporting" / "assets" / (
            bundle.name.removesuffix(".min.js") + ".LICENSES.txt")
        notices = kept.read_text(encoding="utf-8") if kept.is_file() else ""
        # The kept file names the bundle it was written for by its sha256, so the
        # notices of another build cannot stand in for this one's.
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if not header or not version or digest not in notices:
            missing.append(f"plotly's notices could not be written for gui/public/{bundle.name}: "
                           f"{kept.relative_to(root)} is not there, or was written for "
                           "another file (scripts/vendor_plotly_notices.py writes it)")
            continue
        entry = Entry("plotly.js", version.group(1), "MIT")
        entry.lines += ["Licence: MIT", f"File: gui/public/{bundle.name}",
                        "It carries other packages; the terms of each follow plotly's own."]
        entry.texts = [("the bundle's own header", header.group(0).strip() + "\n"),
                       (f"{kept.name}: the terms of plotly.js and of each package the "
                        "bundle carries", notices.rstrip() + "\n")]
        entries.append(entry)
    return entries, missing


def _section(number: int, title: str, entries: list[Entry]) -> str:
    heading = f"{number}. {title}"
    parts = [f"{heading}\n{'=' * len(heading)}\n"]
    for entry in entries:
        parts.append(f"{RULE}\n{entry.name} {entry.version}\n{RULE}\n" + "\n".join(entry.lines) + "\n")
        for title_, text in entry.texts:
            parts.append(f"--- {title_} ---\n{text}")
    return "\n".join(parts)


def render(env_entries: list[Entry], crates: list[Entry], page: list[Entry],
           missing: list[str]) -> str:
    """The whole file. No date in it: the same environment gives the same bytes."""
    npm = sum(1 for e in page if e.name != "plotly.js")
    head = f"""mRNAspect: third-party notices
==============================

This file lists the software of others that this copy of mRNAspect carries, with the
licence each is under and that licence's text. It is generated from the app's own
contents when the app is built (scripts/make_notices.py), not written by hand.

mRNAspect itself is under the MIT licence, which the app's About page prints.

Not in the app, and not covered here: the basecall server (dorado_basecall_server and
ont_basecall_client), its models and MinKNOW are Oxford Nanopore Technologies' software.
The user installs them, mRNAspect calls the server and reads MinKNOW's files, and they
are used under Oxford Nanopore Technologies' own terms.

Contents
--------
1. The Python environment: {len(env_entries)} packages
2. The app window: {len(crates)} Rust crates
3. The page: {npm} npm packages, and plotly.js
"""
    if missing:
        head += "\n" + "\n".join(f"INCOMPLETE: {line}" for line in missing) + "\n"
    return "\n\n".join([head, _section(1, "The Python environment", env_entries),
                        _section(2, "The app window", crates), _section(3, "The page", page)])


def check(env: Path) -> list[str]:
    """Why the notices in ``env`` are not whole; empty when they are."""
    path = env / NAME
    if not path.is_file():
        return [f"{NAME} is not in {env}: staging writes it (scripts/stage_bundle_env.sh)"]
    text = path.read_text()
    found = [line for line in text.splitlines() if line.startswith("INCOMPLETE:")]
    for record in sorted((env / "conda-meta").glob("*.json")):
        data = json.loads(record.read_text())
        if isinstance(data, dict) and data.get("name") and data.get("version"):
            heading = f"{data['name']} {data['version']}"
            if f"\n{RULE}\n{heading}\n{RULE}\n" not in text:
                found.append(f"{heading} is in the environment and not in {NAME}: "
                             "the file was written for another one")
    for package in wheel_installs(env):
        heading = f"{package['name']} {package['version']}"
        if f"\n{RULE}\n{heading}\n{RULE}\n" not in text:
            found.append(f"{heading} is in the environment (installed from a wheel) and "
                         f"not in {NAME}: the file was written for another one")
    return found


def main(argv: list[str]) -> int:
    """Run from the command line; see the module docstring."""
    args = list(argv)
    checking = "--check" in args
    if checking:
        args.remove("--check")
    root = HERE.parent
    if "--root" in args:
        at = args.index("--root")
        if at + 1 >= len(args):
            print("--root needs a folder", file=sys.stderr)
            return 2
        root = Path(args[at + 1])
        del args[at:at + 2]
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: make_notices.py [--check] <env> [--root <checkout>]", file=sys.stderr)
        return 2
    env = Path(args[0])
    if not (env / "conda-meta").is_dir():
        print(f"not a conda environment (no conda-meta/ in it): {env}", file=sys.stderr)
        return 2

    if checking:
        found = check(env)
        for line in found:
            print(f"  FAIL  {line}", file=sys.stderr)
        if not found:
            print(f"  ok    {NAME} is in the environment and lists every package in it")
        return 1 if found else 0

    env_entries, missing = collect_env(env)
    crates, lacking = collect_crates(root)
    missing += lacking
    page, lacking = collect_page(root)
    missing += lacking
    text = render(env_entries, crates, page, missing)
    out = env / NAME
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, out)
    for line in missing:
        print(f"  WARN  {line}", file=sys.stderr)
    npm = sum(1 for e in page if e.name != "plotly.js")
    print(f"  ok    {NAME}: {len(env_entries)} packages, {len(crates)} crates, {npm} npm "
          f"packages, plotly; {len(text) // 1024} KB"
          + (f"; INCOMPLETE in {len(missing)} places (the release check will fail)"
             if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
