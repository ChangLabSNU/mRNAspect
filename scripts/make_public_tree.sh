#!/usr/bin/env bash
# Build the tree that is published, and check it.
#
#   scripts/make_public_tree.sh <out-dir> [<tree-ish>]        (default tree: HEAD)
#
# What is published is an EXPORT of one commit, never this repository made visible. The
# repository's history holds every earlier version of every file, the unpublished ones
# included, so a change of visibility would publish all of it. The export has no past: it
# is the commit's files, less the ones .gitattributes marks `export-ignore`, to be
# committed as the first commit of a new, public repository.
#
# After exporting, the script checks what it made (scripts/check_public_tree.py, run from
# the export's own copy): no file left out by .gitattributes is in it or named in it, and
# none of the rules on what is published is broken. The lab's own names are lent to the
# check from lab_private.json when that file is here; without it that one rule is not asked
# and the script says so.
#
# Nothing here pushes, creates a repository or changes this one.
set -euo pipefail

out=${1:?usage: make_public_tree.sh <out-dir> [<tree-ish>]}
tree=${2:-HEAD}
root=$(git rev-parse --show-toplevel)

if [ -e "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ]; then
  echo "refusing: $out exists and is not empty" >&2
  exit 2
fi
mkdir -p "$out"

# --worktree-attributes is deliberately absent: the export obeys the .gitattributes of the
# commit being exported, so what a commit publishes is decided by that commit.
git -C "$root" archive --format=tar "$tree" | tar -x -C "$out"

# The files the commit marks as not published, by name.
# (A read loop: the Mac's own bash is 3.2 and has no builtin that reads into an array.)
hidden=()
while IFS= read -r path; do
  hidden+=("$path")
done < <(git -C "$root" show "$tree:.gitattributes" \
  | awk '!/^[[:space:]]*#/ && /export-ignore/ { print $1 }')
if [ "${#hidden[@]}" -eq 0 ]; then
  echo "refusing: $tree marks nothing export-ignore, so nothing would be left out" >&2
  exit 2
fi
for path in "${hidden[@]}"; do
  if [ -e "$out/$path" ]; then
    echo "FAILED: $path is in the export" >&2
    exit 1
  fi
done

python=${PYTHON:-python3}
args=("$out" --unpublished "${hidden[@]}")
if [ -f "$root/lab_private.json" ]; then
  args+=(--lab-private "$root/lab_private.json")
fi
"$python" "$out/scripts/check_public_tree.py" "${args[@]}"

echo
echo "exported $(find "$out" -type f | wc -l) files of $tree to $out"
echo "left out: ${hidden[*]}"
