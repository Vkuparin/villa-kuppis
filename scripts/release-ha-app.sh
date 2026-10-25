#!/usr/bin/env bash
# Release the Home Assistant app: point ha-app/build.yaml at the current
# commit, set the version in ha-app/config.yaml, commit and push main.
# Home Assistant then offers the update and builds that commit.
# Usage: scripts/release-ha-app.sh 0.4.1
set -euo pipefail
cd "$(dirname "$0")/.."
version=${1:?usage: $0 VERSION}
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z" >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "commit your changes first" >&2; exit 1; }
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "push main first" >&2; exit 1; }
ref=$(git rev-parse HEAD)
cat > ha-app/build.yaml <<EOF
# Written by scripts/release-ha-app.sh: the commit the app image is built from.
args:
  VK_REF: $ref
EOF
sed -i "s/^version: .*/version: \"$version\"/" ha-app/config.yaml
git add ha-app/build.yaml ha-app/config.yaml
git commit -q -m "Release the Home Assistant app $version"
git push -q origin HEAD:main
echo "Released $version (source $ref); update the app in Home Assistant (Settings > Apps)."
