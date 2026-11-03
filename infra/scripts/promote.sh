#!/usr/bin/env sh
# Moves test or prod to a release tag (PRD H5), or back to the tag it ran before.
#   promote.sh <test|prod> <root> <tag>        (make promote ENV=prod REF=v0.13.0)
#   promote.sh <test|prod> <root> --rollback   (make rollback ENV=prod)
# prod takes only the commit that make mark-passed recorded after test was checked, and its
# database is backed up first. Each environment's previous commit is kept in
# $JAP_ENVS_DIR/<env>.previous for a rollback.
set -eu
ENV_NAME="${1:-}"
ROOT="${2:-}"
REF="${3:-}"
ENVS="${JAP_ENVS_DIR:?}"
case "$ENV_NAME" in test | prod) ;; *) echo "ENV must be test or prod." >&2; exit 1 ;; esac
[ -n "$REF" ] || { echo "Usage: make promote ENV=$ENV_NAME REF=<tag>" >&2; exit 1; }
[ -f "$ROOT/.env" ] || { echo "No $ENV_NAME environment yet. Run: make env-create ENV=$ENV_NAME REF=$REF" >&2; exit 1; }

PREVIOUS_FILE="$ENVS/$ENV_NAME.previous"
CURRENT="$(git -C "$ROOT" rev-parse HEAD)"

if [ "$REF" = "--rollback" ]; then
  [ -f "$PREVIOUS_FILE" ] || { echo "Nothing to roll back to: $ENV_NAME has not been promoted yet." >&2; exit 1; }
  TARGET="$(cat "$PREVIOUS_FILE")"
  echo "Rolling $ENV_NAME back to $(git describe --tags --always "$TARGET")."
  echo "If the newer tag changed the database, restore the backup taken before it: make restore ENV=$ENV_NAME FILE=..."
else
  git fetch --tags --quiet origin 2>/dev/null || echo "Could not fetch tags from origin; using the tags already here."
  TARGET="$(git rev-parse --verify --quiet "$REF^{commit}")" || { echo "No tag or commit $REF." >&2; exit 1; }
  if [ "$ENV_NAME" = prod ]; then
    PASSED="$(cat "$ENVS/test.passed" 2>/dev/null || true)"
    if [ "$PASSED" != "$TARGET" ]; then
      echo "$REF has not passed test. Run make promote ENV=test REF=$REF, check it, then make mark-passed." >&2
      exit 1
    fi
  fi
fi

if [ "$ENV_NAME" = prod ] && make --no-print-directory ps ENV=prod 2>/dev/null | grep -q ' db'; then
  echo "Backing up prod before the change."
  make --no-print-directory backup ENV=prod
fi

[ "$CURRENT" = "$TARGET" ] || echo "$CURRENT" > "$PREVIOUS_FILE"
git -C "$ROOT" checkout --quiet --detach "$TARGET"
echo "$ENV_NAME is now on $(git -C "$ROOT" describe --tags --always). Building and starting."
make --no-print-directory start ENV="$ENV_NAME"
[ "$ENV_NAME" = test ] && echo "Check it at https://test.jap.tutorial.monster, then run: make mark-passed"
exit 0
