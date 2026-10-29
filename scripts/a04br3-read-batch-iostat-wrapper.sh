#!/bin/bash
# The launcher redirects stdout/stderr to the unique run.log before startup.
set -u
: "${IO_TASK:?}" "${IO_ATTEMPT:?}" "${RELEASE:?}" "${GITHUB_REPOSITORY:?}"
case "$IO_ATTEMPT" in *[!a-zA-Z0-9-]*|'') echo 'Invalid attempt identifier' >&2; exit 2 ;; esac
prefix="io-batch-iostat-${IO_ATTEMPT}"
log="$IO_TASK/$prefix-run.log"
statusfile="$IO_TASK/$prefix-status.txt"
[ -f "$log" ] && [ ! -e "$statusfile" ] || { echo 'Missing run log or pre-existing status' >&2; exit 73; }
# No clobber. The launcher also reserves a unique task and attempt; this
# independent check rejects any already published artifact for the prefix.
assets=$(gh release view "$RELEASE" -R "$GITHUB_REPOSITORY" --json assets) || { echo 'Cannot inspect release assets' >&2; exit 74; }
if ! printf '%s' "$assets" | python3 -c 'import json,sys; p=sys.argv[1]+"-"; x=json.load(sys.stdin); sys.exit(any(a["name"].startswith(p) for a in x["assets"]))' "$prefix"; then
  echo 'Release asset prefix already used or malformed' >&2; exit 73
fi
bash "$IO_TASK/runner.sh" "$IO_TASK"
status=$?
if [ "$status" -eq 0 ]; then
  archive="$IO_TASK/$prefix-artifacts.tgz"
  if tar -C "$IO_TASK" -czf "$archive" pair && gh release upload "$RELEASE" "$archive" -R "$GITHUB_REPOSITORY"; then
    echo IOSTAT_ARCHIVE_UPLOADED
  else
    status=74
  fi
fi
# Publish the log first: status=0 is authoritative only after both archive
# and log uploads succeed. A failed status upload leaves a local failure.
if ! gh release upload "$RELEASE" "$log" -R "$GITHUB_REPOSITORY"; then
  printf 'exit=74\n' > "$statusfile" || true
  exit 74
fi
printf 'exit=%s\n' "$status" > "$statusfile" || exit 74
if ! gh release upload "$RELEASE" "$statusfile" -R "$GITHUB_REPOSITORY"; then
  printf 'exit=74\n' > "$statusfile" || true
  echo 'Status upload outcome uncertain; inspect the remote asset and local status' >&2
  exit 74
fi
exit "$status"
