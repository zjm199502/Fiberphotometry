#!/usr/bin/env bash
set -euo pipefail
dest="$RUNNER_TEMP/fiberlume-channel"
url="https://github.com/$GITHUB_REPOSITORY.git"
mkdir -p "$dest"
git -C "$dest" init -b updates
git -C "$dest" remote add origin "$url"
# Reuse checkout's authenticated config without copying the credential to disk.
git_channel() { git -C "$dest" -c "include.path=$GITHUB_WORKSPACE/.git/config" "$@"; }
if git ls-remote --heads origin updates | grep -q refs/heads/updates; then
  git_channel fetch origin refs/heads/updates
  git -C "$dest" checkout -B updates FETCH_HEAD
fi
PYTHONPATH=src python packaging/patch_channel.py publish "$dest"
git -C "$dest" add -- channel.json
for zip in "$dest"/*.zip; do
  if [[ -f "$zip" ]]; then git -C "$dest" add -- "$(basename "$zip")"; fi
done
if ! git -C "$dest" diff --cached --quiet; then
  git -C "$dest" -c user.name=github-actions -c user.email=github-actions@users.noreply.github.com commit -m "Update FiberLume channel $GITHUB_SHA"
  git_channel push origin HEAD:refs/heads/updates
fi
