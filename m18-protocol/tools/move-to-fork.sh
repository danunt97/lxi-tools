#!/usr/bin/env bash
# Moves this project (m18-protocol/ inside danunt97/lxi-tools) into the fork
# danunt97/m18-protocol as branch "battery-management" - without touching
# upstream files beyond the small, intended changes.
#
#   1. Fork https://github.com/mnh-jansson/m18-protocol on GitHub (button "Fork").
#   2. From inside the lxi-tools checkout (branch claude/m18-battery-management-gui-63bv0l):
#        bash m18-protocol/tools/move-to-fork.sh
#      (Windows: run it in "Git Bash")
#   3. Open a pull request inside your fork, or make the branch the default.
set -euo pipefail

FORK_URL="${1:-https://github.com/danunt97/m18-protocol.git}"
BRANCH="${2:-battery-management}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"          # .../lxi-tools/m18-protocol
LXI="$(cd "$SRC/.." && pwd)"
BASE_COMMIT="ea3e5f7"                             # import of upstream m18-protocol into lxi-tools

WORK="$(mktemp -d)"
git clone "$FORK_URL" "$WORK/fork"
cd "$WORK/fork"
git checkout -b "$BRANCH"

# 1) new parts of the project (copied as they are)
for d in gui esp32-bridge tools; do
  rm -rf "./$d"
  cp -R "$SRC/$d" "./$d"
done
cp "$SRC/m18_bridge.py" ./m18_bridge.py
find . -name __pycache__ -prune -exec rm -rf {} +

# 2) changes to upstream files (m18.py, README.md, .gitignore) as a patch, so newer
#    upstream commits in the fork are kept and only our edits are applied on top
git -C "$LXI" diff "$BASE_COMMIT" HEAD -- m18-protocol/m18.py m18-protocol/README.md m18-protocol/.gitignore \
  > "$WORK/upstream-changes.patch"
git apply -p2 --3way "$WORK/upstream-changes.patch" || {
  echo "Patch für m18.py/README.md/.gitignore ließ sich nicht automatisch anwenden."
  echo "Bitte Konflikte in $WORK/fork lösen, dann: git add -A && git commit && git push -u origin $BRANCH"
  exit 1
}

git add -A
git commit -m "Battery management: web GUI with SQLite fleet database, aging alerts, QR labels, ESP32-S3 bridge"
git push -u origin "$BRANCH"
echo "Fertig: $FORK_URL Branch $BRANCH"
