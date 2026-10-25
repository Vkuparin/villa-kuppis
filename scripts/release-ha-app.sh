#!/usr/bin/env bash
# Release the Home Assistant app: set ha-app/config.yaml's version, commit,
# tag ha-app-<version> and push. Home Assistant then offers the update and
# builds that tag. Usage: scripts/release-ha-app.sh 0.4.1
set -euo pipefail
cd "$(dirname "$0")/.."
version=${1:?usage: $0 VERSION}
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z" >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "commit your changes first" >&2; exit 1; }
if ! grep -qx "version: \"$version\"" ha-app/config.yaml; then
    sed -i "s/^version: .*/version: \"$version\"/" ha-app/config.yaml
    git commit -q -m "Release the Home Assistant app $version" ha-app/config.yaml
fi
git tag "ha-app-$version"
git push origin HEAD:main "ha-app-$version"
echo "Tagged ha-app-$version; update the app in Home Assistant (Settings > Apps)."
