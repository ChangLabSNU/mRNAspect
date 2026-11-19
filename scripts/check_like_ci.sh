#!/usr/bin/env bash
#
# Run what CI runs, on this machine. One command, because the thing CI gives that a
# person cannot is not thoroughness — it is that it happens whether or not anyone
# remembers, and the only defence against forgetting is making it cheap.
#
# Written 2026-10-27, when GitHub Actions stopped starting jobs ("recent account payments
# have failed or your spending limit needs to be increased"). Until that is settled this
# script is the only clean-environment check there is.
#
# WHAT IT REPRODUCES, and why each part is not optional:
#
#   fresh clone      CI checks out the commit, not your working tree. Running the suite
#                    in place passes on files you have not committed. A fresh clone of
#                    THIS repo found two real defects on 2026-10-23 that reading did not.
#   frozen install   `--frozen` installs exactly the lock. Letting pixi re-solve means
#                    testing an environment nobody will ship.
#   lock gate        `pixi lock --check` first, separately: a pixi.toml edited without
#                    re-locking makes every later check test the wrong environment AND
#                    PASS. That is why CI runs it as its own job, ahead of the tests.
#   sharded suite    the same command CI uses, so a shard-split bug shows up here too.
#
# WHAT IT CANNOT REPRODUCE: the other platform. On a Mac this is the macOS half, which is
# the deployment target and the more important one; Linux then has no coverage, and vice
# versa. Say which one you ran.
#
#   bash scripts/check_like_ci.sh [shards]
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARDS="${1:-4}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mrnaspect-ci.XXXXXX")"

# On failure, print the shard logs and KEEP the directory. The first version deleted it
# unconditionally, so the suite failed, told the reader to go and look at
# .pytest_shards/shard_*.log, and had already removed them -- advice pointing at
# something that no longer existed. CI dumps them in a `if: failure()` step; this is that
# step. Found by running the script instead of shipping it unrun.
cleanup() {
  local code=$?
  if [ "$code" -ne 0 ] && [ -d "$WORK/clone/.pytest_shards" ]; then
    echo
    echo "==> shard logs (tail 60 each)"
    for f in "$WORK/clone/.pytest_shards"/shard_*.log; do
      echo "--- $f"; tail -60 "$f"
    done
    echo
    echo "==> kept for inspection: $WORK/clone"
    echo "    Re-run a failing file alone before believing it: a shard-only failure is"
    echo "    usually two shards contending, not a defect."
    return
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "==> platform: $(uname -s) $(uname -m)"
echo "==> source:   $ROOT"

# Uncommitted work is exactly what a fresh clone does not see, so say so rather than
# letting a green run imply the tree is clean.
if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    echo
    echo "    NOTE: the working tree has uncommitted changes. They are NOT in this check —"
    echo "    a clone only carries what is committed. That is the point, and it is also"
    echo "    the thing to remember when this passes."
    git -C "$ROOT" status --short | sed 's/^/      /'
fi

echo
echo "==> cloning HEAD into a directory this repo has never seen"
git clone -q "$ROOT" "$WORK/clone"
cd "$WORK/clone"
echo "    $(git log -1 --oneline)"

# The pin CI uses, read from the workflow so the two cannot drift apart.
PINNED="$(sed -n 's/.*pixi-version: v\(.*\)/\1/p' "$ROOT/.github/workflows/ci.yml" | head -1)"
HAVE="$(pixi --version | awk '{print $2}')"

echo
echo "==> lock is in sync"
if [ "$HAVE" = "$PINNED" ]; then
    pixi lock --check
else
    # NOT a failure. ci.yml pins pixi for exactly this reason, in its own words: a newer
    # pixi rewrites the lock into a newer format BEFORE comparing, so `--check` reports
    # drift from a lock that is byte-identical in content -- and "a gate that fires on a
    # tool upgrade instead of on lock drift teaches people to ignore it".
    #
    # So it is skipped, loudly, rather than failed. The first version of this script did
    # not pin and did not know, and reported a Mac as broken on 2026-10-27 when nothing
    # was.
    echo "    SKIPPED: pixi $HAVE here, $PINNED in CI."
    echo "    A newer pixi re-solves the lock to a newer FORMAT before comparing and then"
    echo "    reports drift that is not there. The gate catches a pixi.toml edited without"
    echo "    re-locking, so it is still worth running:"
    echo "        pixi self-update --version $PINNED   # then re-run this script"
    echo "    Upgrading the lock format is a deliberate change: bump the pin in ci.yml and"
    echo "    re-lock, do not let a tool do it."
fi

echo
echo "==> installing the locked environment (frozen)"
pixi install --frozen

echo
echo "==> dev-install"
pixi run dev-install

echo
echo "==> suite, $SHARDS shards"
bash scripts/run_tests_sharded.sh .pytest_shards "$SHARDS"

echo
echo "==> PASSED on $(uname -s) $(uname -m) at $(git log -1 --format=%h)"
[ "$HAVE" = "$PINNED" ] || echo "    (the lock gate was SKIPPED -- pixi $HAVE, not $PINNED)"
echo "    The other platform is still unchecked. Until Actions runs again, a release"
echo "    needs this on BOTH a Mac and a Linux box."
