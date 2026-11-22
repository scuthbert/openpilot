#!/usr/bin/env bash
# Rebuild tq-wb-prebuilt: EVPilot's prebuilt release (rel) with the torque-only
# Rivian code from EVPilot's torque branch, so it runs without the angle kit and
# installs without compiling on the device.
#
# The torque-only files are 3-way merged (torque, the rel of the last build, the
# new rel), so fixes rel makes in them are kept. Stops without publishing if
# torque gains code rel doesn't have, if a merge conflicts, or if the car safety
# code differs.
set -euo pipefail

UPSTREAM="${UPSTREAM:-https://github.com/evpilotgo/openpilot.git}"
REL="${REL:-rel}"
TORQUE="${TORQUE:-torque}"
OUT="${OUT:-tq-wb-prebuilt}"
ORIGIN="${ORIGIN:?set ORIGIN to the push URL of your fork}"
FORCE="${FORCE:-0}"

# torque-only Rivian code, copied from torque onto rel
OVERLAY=(
  opendbc_repo/opendbc/car/rivian/carcontroller.py
  opendbc_repo/opendbc/car/rivian/ext_controller.py
  opendbc_repo/opendbc/car/rivian/interface.py
  openpilot/selfdrive/controls/controlsd.py
)

work="$(mktemp -d)"
tmp="$(mktemp -d)"  # scratch files for the merges, outside the repo
trap 'rm -rf "$work" "$tmp"' EXIT
cd "$work"
git init -q
git config user.name "${GIT_AUTHOR_NAME:-tq-wb-prebuilt sync}"
git config user.email "${GIT_AUTHOR_EMAIL:-tq-wb-prebuilt-sync@users.noreply.github.com}"
git remote add up "$UPSTREAM"
git remote add origin "$ORIGIN"

git fetch -q --depth 1 up "+refs/heads/$REL:refs/remotes/up/$REL" "+refs/heads/$TORQUE:refs/remotes/up/$TORQUE"
rel_sha="$(git rev-parse "up/$REL")"
torque_sha="$(git rev-parse "up/$TORQUE")"
echo "rel    $rel_sha"
echo "torque $torque_sha"

# the last published build records the rel and torque commits it came from
base_rel="${BASE_REL:-}"
base_torque="${BASE_TORQUE:-}"
if git fetch -q --depth 1 origin "+refs/heads/$OUT:refs/remotes/origin/$OUT" 2>/dev/null; then
  msg="$(git log -1 --format=%B "origin/$OUT")"
  # skip when the published build already comes from these two commits
  if [ "$FORCE" != "1" ] && grep -q "Built-From-Rel: $rel_sha" <<<"$msg" && grep -q "Built-From-Torque: $torque_sha" <<<"$msg"; then
    echo "up to date, nothing to do"
    exit 0
  fi
  [ -n "$base_rel" ] || base_rel="$(sed -n 's/^Built-From-Rel: //p' <<<"$msg" | tail -1)"
  [ -n "$base_torque" ] || base_torque="$(sed -n 's/^Built-From-Torque: //p' <<<"$msg" | tail -1)"
fi
if [ -z "$base_rel" ] || [ -z "$base_torque" ]; then
  echo "::error::no previous build to merge from, set BASE_REL and BASE_TORQUE to a rel/torque pair known to go together"
  exit 1
fi
# full objects: a partial (--filter) fetch makes up a promisor remote, and the push then tries to
# lazily fetch objects only the fork has from it ("not our ref")
git fetch -q --depth 1 up "$base_rel" "$base_torque"
echo "base rel    $base_rel"
echo "base torque $base_torque"

# write a file as it is at a commit, empty if it doesn't exist there
blob() { git show "$1:$2" 2>/dev/null || true; }
is_overlay() { local o; for o in "${OVERLAY[@]}"; do [ "$o" = "$1" ] && return 0; done; return 1; }

# guard 1: every change torque made since the last build, outside the overlay, must already be in rel.
# rel being ahead of torque is fine (we ship rel's files), torque gaining code that rel lacks is not.
missing=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  is_overlay "$f" && continue
  [ "$f" = .gitmodules ] && continue                                              # rel vendors submodules
  [ "$(git ls-tree "up/$TORQUE" -- "$f" | cut -d' ' -f1)" = 160000 ] && continue  # submodule pointer
  blob "up/$REL" "$f" > "$tmp/rel"
  blob "$base_torque" "$f" > "$tmp/base"
  blob "up/$TORQUE" "$f" > "$tmp/torque"
  cp "$tmp/rel" "$tmp/merged"
  if ! git merge-file -q "$tmp/merged" "$tmp/base" "$tmp/torque" || ! cmp -s "$tmp/merged" "$tmp/rel"; then
    missing+="$f"$'\n'
  fi
done < <(git diff --name-only "$base_torque" "up/$TORQUE")
if [ -n "$missing" ]; then
  echo "::error::torque changed files since the last build in ways rel doesn't have, not publishing:"
  printf '%s' "$missing"
  exit 1
fi

# guard 2: car safety code (built into the prebuilt panda firmware) must be identical
if [ -n "$(git diff --name-only "up/$TORQUE" "up/$REL" -- opendbc_repo/opendbc/safety)" ]; then
  echo "::error::opendbc safety code differs between torque and rel, the prebuilt firmware would not match"
  exit 1
fi

git checkout -q -B "$OUT" "up/$REL"

# overlay: 3-way merge, keep torque's version and bring in what rel changed since the last build,
# so a rel fix in one of these files is no longer overwritten by an older torque copy
conflicts=""
for f in "${OVERLAY[@]}"; do
  blob "up/$TORQUE" "$f" > "$tmp/torque"
  blob "$base_rel" "$f" > "$tmp/base"
  if git merge-file -q -L torque -L "base rel" -L rel "$tmp/torque" "$tmp/base" "$f"; then
    cp "$tmp/torque" "$f"
  else
    conflicts+="$f"$'\n'
  fi
done
if [ -n "$conflicts" ]; then
  echo "::error::rel and torque both changed these torque-only files in overlapping places, merge them by hand:"
  printf '%s' "$conflicts"
  exit 1
fi

# only wait for Chestnut when one is actually attached, not for any cable in the aux port
python3 - openpilot/selfdrive/modeld/modeld.py openpilot/sunnypilot/modeld_v2/modeld.py <<'PY'
import os, re, sys
# (chestnut_present() or cable_connected()), or with a condition on the cable such as
# (chestnut_present() or (cable_connected() and not jetlink_adapter.link_on())), possibly across a line continuation
CABLE = re.compile(r"\(\s*chestnut_present\(\)\s+or\s+(?:\\\s*)?(?:cable_connected\(\)|\(\s*cable_connected\(\)[^()]*(?:\([^()]*\)[^()]*)*\))\s*\)")
for path in sys.argv[1:]:
  if not os.path.exists(path):
    continue
  src = open(path).read()
  src = CABLE.sub("chestnut_present()", src)
  m = re.search(r"^\s*CHESTNUT = (?:.*\\\n)*.*$", src, re.M)  # the whole statement, continuations included
  if m is None or "cable_connected" in m.group(0) or re.search(r"\bor\b", m.group(0)):  # anything besides chestnut_present()
    sys.exit(f"::error::{path}: Chestnut check changed upstream and wasn't patched, review it")
  open(path, "w").write(src)
  print(f"{path}: {m.group(0).strip()}")
PY

# drop Git LFS pointers (the 800 MB Chestnut model stalls the installer), and upstream workflows
lfs_pointers="$(git ls-files -z | { xargs -0 -n 200 grep -l -s -m1 '^version https://git-lfs.github.com/spec/v1' -- || true; })"
while read -r p; do
  [ -n "$p" ] && git rm -q -- "$p"
done <<<"$lfs_pointers"
git rm -q -r --ignore-unmatch .gitattributes .lfsconfig .github

# fail fast on a syntax error
python3 -m py_compile "${OVERLAY[@]}" openpilot/selfdrive/modeld/modeld.py openpilot/sunnypilot/modeld_v2/modeld.py

git add -A
# a snapshot with no parent: evpilotgo/openpilot isn't in this fork's network, so GitHub doesn't have rel's
# history and rejects a shallow push whose parent it never saw. The comma installs and updates the branch as a snapshot.
build="$(git commit-tree "$(git write-tree)" -F - <<EOF
tq-wb prebuilt: EVPilot rel with the torque-only Rivian code

Merges torque's carcontroller.py, ext_controller.py, interface.py and
controlsd.py onto the prebuilt rel (no angle-upgrade dashcam gate), keeping
rel's changes to them since the last build. Only waits for Chestnut when one
enumerates, and drops the LFS big-model pointers.

Built-From-Rel: $rel_sha
Built-From-Torque: $torque_sha
EOF
)"
git checkout -q -B "$OUT" "$build"

if [ "${DRY_RUN:-0}" = "1" ]; then
  git show --stat --format='%B' HEAD | tail -20
  exit 0
fi
git push -q --force --no-verify origin "$OUT:refs/heads/$OUT"  # skip the git-lfs pre-push hook; rel's history has LFS pointers we don't ship
echo "published $OUT $(git rev-parse HEAD)"
