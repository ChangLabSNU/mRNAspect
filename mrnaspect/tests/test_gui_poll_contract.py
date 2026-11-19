"""The Rust `Poll` struct and the TypeScript `Poll` type are one contract, checked here.

`poll_run` serializes a Rust struct that the GUI's TypeScript deserializes by structural
type. Nothing enforces that the two agree: serde renames nothing, TypeScript trusts the
`invoke<Poll>` annotation, and neither compiler can see the other side. A field added to
one and not the other is `undefined` at run time, on the operator's machine.

This is checkable on the Linux suite host and nowhere else in CI, because **neither
toolchain exists here** — no `cargo` (no webkit2gtk, no root) and no node/npm. So the
Mac is the only place the two halves are compiled, and it is the last place a mismatch
should be found.

The guard that made this urgent: a stop pressed in pod5 mode looked like a dead button,
because the poll reported `state: "running"` while Core finished the in-flight POD5 and
the UI repainted over its own "stopping…" message. Fixing it added a state AND a field,
one on each side of this contract.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RUST = REPO / "gui" / "src-tauri" / "src" / "lib.rs"
TS = REPO / "gui" / "src" / "main.ts"


def _rust_poll_fields() -> list[str]:
    body = re.search(r"pub struct Poll \{(.*?)\n\}", RUST.read_text(), re.S)
    assert body, "Rust `pub struct Poll` not found — did it move or get renamed?"
    out = []
    for line in body.group(1).splitlines():
        line = line.strip()
        # Skip doc comments, ordinary comments and attributes; keep `name: Type,`.
        if not line or line.startswith(("//", "#[")):
            continue
        m = re.match(r"([A-Za-z_]\w*)\s*:", line)
        if m:
            out.append(m.group(1))
    return out


def _ts_poll_fields() -> list[str]:
    body = re.search(r"type Poll = \{(.*?)\n\};", TS.read_text(), re.S)
    assert body, "TypeScript `type Poll` not found — did it move or get renamed?"
    out = []
    for line in body.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        m = re.match(r"([A-Za-z_]\w*)\??\s*:", line)
        if m:
            out.append(m.group(1))
    return out


def _state_assignments(src: str) -> list[str]:
    r"""Every `state: <expr>` in the Rust source, as source text.

    Brace-balanced rather than a literal match, because the state is produced by a
    CONDITIONAL — `state: if finished { "finished" } else if … { "stopping" } else
    { "running" },`. A regex for `state:\s*"(\w+)"` sees only the one plain-literal
    assignment and silently reports that the others do not exist; it passed a mutation
    that deleted "stopping" from the TypeScript union, which is the exact bug this file
    was written for.
    """
    spans = []
    for m in re.finditer(r"\bstate:\s*", src):
        i, depth = m.end(), 0
        while i < len(src):
            c = src[i]
            if c in "{(":
                depth += 1
            elif c in "})":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                break
            i += 1
        spans.append(src[m.end():i])
    return spans


def test_both_sides_declare_fields():
    """A contract test that parses nothing passes forever."""
    assert len(_rust_poll_fields()) >= 5, _rust_poll_fields()
    assert len(_ts_poll_fields()) >= 5, _ts_poll_fields()


def test_poll_fields_match_exactly():
    rust, ts = set(_rust_poll_fields()), set(_ts_poll_fields())
    assert rust == ts, (
        f"Poll contract drifted. Only in Rust: {sorted(rust - ts)}; "
        f"only in TypeScript: {sorted(ts - rust)}. A field on one side alone arrives as "
        f"`undefined` in the GUI, and no compiler on any host sees both sides.")


def test_every_state_rust_emits_is_handled_in_typescript():
    """`state` is a Rust &'static str against a TS string-literal union."""
    emitted = set()
    for span in _state_assignments(RUST.read_text()):
        emitted.update(re.findall(r'"(\w+)"', span))
    union = re.search(r"state:\s*((?:\"\w+\"\s*\|?\s*)+);", TS.read_text())
    assert union, "TypeScript `state` union not found"
    declared = set(re.findall(r'"(\w+)"', union.group(1)))
    assert emitted, "no state literals found in the Rust poll"
    missing = emitted - declared
    assert not missing, (
        f"Rust can emit state(s) {sorted(missing)} that the TypeScript union does not "
        f"declare, so renderStatus falls through to its terminal branch and reports a "
        f"live run as finished.")


# --- the command names ------------------------------------------------------------

def test_every_command_the_frontend_invokes_is_registered_in_rust():
    """`invoke("name")` is a string on both sides.

    Tauri resolves it at run time: an unregistered name rejects the promise, so a typo
    shows up as a button that does nothing, or — for `read_figures` — as a charts panel
    that silently never appears. Neither compiler sees both ends, and neither compiler
    runs on this host at all.
    """
    rust = RUST.read_text()
    handler = re.search(r"generate_handler!\[(.*?)\]", rust, re.S)
    assert handler, "tauri::generate_handler![...] not found"
    registered = set(re.findall(r"[A-Za-z_]\w*", handler.group(1)))
    invoked = set(re.findall(r'invoke(?:<[^>]*>)?\(\s*"(\w+)"', TS.read_text()))
    assert invoked, "no invoke() calls found in main.ts"
    missing = invoked - registered
    assert not missing, (
        f"the frontend invokes {sorted(missing)}, which Rust does not register; "
        f"registered: {sorted(registered)}")


def test_every_registered_command_is_defined():
    """A name in the handler list that is not a function fails to compile on the Mac."""
    rust = RUST.read_text()
    handler = re.search(r"generate_handler!\[(.*?)\]", rust, re.S)
    registered = set(re.findall(r"[A-Za-z_]\w*", handler.group(1)))
    defined = set(re.findall(r"\nfn (\w+)\(", rust))
    assert registered <= defined, sorted(registered - defined)
