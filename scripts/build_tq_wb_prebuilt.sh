#!/usr/bin/env bash
# Rebuild tq-wb-prebuilt: EVPilot's prebuilt release (rel) with the torque-only
# Rivian code from EVPilot's torque branch, so it runs without the angle kit and
# installs without compiling on the device.
#
# Stops without publishing if torque and rel differ anywhere beyond the expected
# files, because then copying files over the prebuilt might no longer be safe.
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
# files allowed to differ between torque and rel (beyond whitespace)
ALLOWED=("${OVERLAY[@]}" openpilot/selfdrive/ui/mici/onroad/torque_bar.py)

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
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

# skip when the published build already comes from these two commits
if [ "$FORCE" != "1" ] && git fetch -q --depth 1 origin "+refs/heads/$OUT:refs/remotes/origin/$OUT" 2>/dev/null; then
  msg="$(git log -1 --format=%B "origin/$OUT")"
  if grep -q "Built-From-Rel: $rel_sha" <<<"$msg" && grep -q "Built-From-Torque: $torque_sha" <<<"$msg"; then
    echo "up to date, nothing to do"
    exit 0
  fi
fi

# guard 1: Python in openpilot/ and opendbc_repo/ differs only in the allowed files
unexpected="$(git diff -w --name-only "up/$TORQUE" "up/$REL" -- 'openpilot/*.py' 'opendbc_repo/opendbc/*.py' \
  | grep -v -E '/tests?/|/test_[^/]*\.py$|^openpilot/tools/|/debug/' \
  | grep -v -x -F -f <(printf '%s\n' "${ALLOWED[@]}") || true)"
if [ -n "$unexpected" ]; then
  echo "::error::torque and rel differ in files beyond the expected ones, not publishing:"
  echo "$unexpected"
  exit 1
fi
# guard 2: car safety code (built into the prebuilt panda firmware) must be identical
if [ -n "$(git diff --name-only "up/$TORQUE" "up/$REL" -- opendbc_repo/opendbc/safety)" ]; then
  echo "::error::opendbc safety code differs between torque and rel, the prebuilt firmware would not match"
  exit 1
fi

git checkout -q -B "$OUT" "up/$REL"
for f in "${OVERLAY[@]}"; do
  git show "up/$TORQUE:$f" > "$f"
done

# only wait for Chestnut when one is actually attached, not for any cable in the aux port
for f in openpilot/selfdrive/modeld/modeld.py openpilot/sunnypilot/modeld_v2/modeld.py; do
  [ -f "$f" ] || continue
  sed -i -E 's/\(chestnut_present\(\) or cable_connected\(\)\)/chestnut_present()/' "$f"
  if grep -n "CHESTNUT = " "$f" | grep -q "cable_connected"; then
    echo "::error::$f: Chestnut check changed upstream and wasn't patched, review it"
    exit 1
  fi
done

# drop Git LFS pointers (the 800 MB Chestnut model stalls the installer), and upstream workflows
lfs_pointers="$(git ls-files -z | { xargs -0 -n 200 grep -l -s -m1 '^version https://git-lfs.github.com/spec/v1' -- || true; })"
while read -r p; do
  [ -n "$p" ] && git rm -q -- "$p"
done <<<"$lfs_pointers"
git rm -q -r --ignore-unmatch .gitattributes .lfsconfig .github

# fail fast on a syntax error
python3 -m py_compile "${OVERLAY[@]}" openpilot/selfdrive/modeld/modeld.py openpilot/sunnypilot/modeld_v2/modeld.py

git add -A
git commit -q -m "tq-wb prebuilt: EVPilot rel with the torque-only Rivian code

Copies carcontroller.py, ext_controller.py, interface.py and controlsd.py
from torque onto the prebuilt rel (no angle-upgrade dashcam gate). Only
waits for Chestnut when one enumerates, and drops the LFS big-model pointers.

Built-From-Rel: $rel_sha
Built-From-Torque: $torque_sha"

if [ "${DRY_RUN:-0}" = "1" ]; then
  git show --stat --format='%B' HEAD | tail -20
  exit 0
fi
git push -q --force origin "$OUT:refs/heads/$OUT"
echo "published $OUT $(git rev-parse HEAD)"
