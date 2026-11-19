#!/usr/bin/env bash
#
# Run the test suite as N parallel processes ("shards").
#
# Why this exists. The suite is 649 tests and takes ~14m30s in one process, which is slow
# enough that it stops being run — and a suite that is skipped is worse than a slow one.
# Almost none of that is computation: most of the cost is end-to-end tests that each build
# and run a whole OfflinePipeline (~4s apiece), so the process spends its time waiting on
# I/O and subprocesses while 71 other cores sit idle. Measured on a 72-core host:
# 14m30s -> 4m35s with 6 shards, 3.2x.
#
# Why the split is BY FILE, not by test. Several modules bind a TCP port or start a
# dorado-server stand-in. Two shards doing that at once collide, and the collision looks
# exactly like a flaky test — the first run of this script surfaced one real instance
# (see the commit "Stop a reachability test from racing anyone for a freed port"). Every
# file matching PINNED_PATTERN below is kept together in shard 0 and never spread.
#
# This is NOT a replacement for `pixi run test`. It is the same tests in a different
# number of processes; if a result differs between the two, the shard split is at fault
# and the serial run is the authority.
#
# usage: scripts/run_tests_sharded.sh [out_dir] [n_shards]
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

OUT="${1:-.pytest_shards}"
N="${2:-6}"
# One glob per element. NOT a single 'a|b|c' string in a variable: bash parses the `|`
# alternation of a `case` pattern at parse time, so alternation that arrives by variable
# expansion silently matches nothing. That is how the first version of this script shipped
# with every port-using file UNPINNED while printing that it had pinned them.
PINNED_PATTERNS=(
  '*dorado_server*' '*realtime*' '*watcher*' '*backpressure*'
  '*sealing*' '*mimic*' '*progress*'
)

mkdir -p "$OUT"
rm -f "$OUT"/shard_*.log "$OUT"/shard_*.rc

# NOT `mapfile`. macOS ships bash 3.2 — Apple froze it at the last GPLv2 release — and
# `mapfile` is a bash 4 builtin, so on a Mac this script died at its first line of work
# with "mapfile: command not found" followed by two unbound-variable errors. Found by the
# first CI run, on the macOS job, which is the platform the product is deployed to.
ALL=()
while IFS= read -r f; do
  ALL+=("$f")
done < <(ls mrnaspect/tests/test_*.py tests/test_*.py 2>/dev/null)
if [ "${#ALL[@]}" -eq 0 ]; then
  echo "no test files found under mrnaspect/tests or tests — wrong directory?" >&2
  exit 2
fi

PINNED=(); REST=()
for f in "${ALL[@]}"; do
  pinned=0
  for pat in "${PINNED_PATTERNS[@]}"; do
    # shellcheck disable=SC2053  # unquoted RHS is the glob match, on purpose
    if [[ "$f" == $pat ]]; then pinned=1; break; fi
  done
  if [ "$pinned" -eq 1 ]; then PINNED+=("$f"); else REST+=("$f"); fi
done

# A silent zero here means the patterns stopped matching the layout (files renamed or
# moved), which costs nothing today and produces an unreproducible failure later.
if [ "${#PINNED[@]}" -eq 0 ]; then
  echo "refusing to run: no test file matched PINNED_PATTERNS, so the port-using files" >&2
  echo "would be spread across shards. Update PINNED_PATTERNS to match the layout." >&2
  exit 2
fi

declare -a SHARD
SHARD[0]="${PINNED[*]}"
i=1
for f in "${REST[@]}"; do
  k=$(( i % N )); [ "$k" -eq 0 ] && k=$(( N - 1 ))
  SHARD[$k]="${SHARD[$k]:-} $f"
  i=$(( i + 1 ))
done

echo "running ${#ALL[@]} test files in $N shards (shard 0 holds ${#PINNED[@]} port-using files)"
start=$SECONDS
for k in $(seq 0 $(( N - 1 ))); do
  [ -z "${SHARD[$k]:-}" ] && continue
  (
    # -p no:cacheprovider: shards share a working directory, and one .pytest_cache
    # written by six processes is a race that produces no useful information anyway.
    pixi run -e default python -m pytest ${SHARD[$k]} -q -p no:cacheprovider \
      > "$OUT/shard_$k.log" 2>&1
    echo "$?" > "$OUT/shard_$k.rc"
  ) &
done
wait
elapsed=$(( SECONDS - start ))

rc=0
total=0
for f in "$OUT"/shard_*.rc; do
  k="$(basename "$f" .rc)"
  code="$(cat "$f")"
  line="$(grep -E '^[0-9]+ (passed|failed)|passed|failed|error' "$OUT/$k.log" | tail -1)"
  n="$(sed -n 's/^\([0-9]\+\) passed.*/\1/p' <<< "$line")"
  [ -n "$n" ] && total=$(( total + n ))
  printf '%-9s exit %-3s %s\n' "$k" "$code" "$line"
  [ "$code" != "0" ] && rc=1
done

echo "----"
if [ "$rc" -eq 0 ]; then
  echo "$total passed in ${elapsed}s across $N shards"
else
  echo "FAILURES — see $OUT/shard_*.log"
  echo "Before treating one as real, re-run that file alone: a shard-only failure is"
  echo "usually two shards contending, not a defect. Add the file to PINNED_PATTERN."
fi
exit "$rc"
