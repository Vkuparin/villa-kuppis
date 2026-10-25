#!/bin/sh
# Install or update the Villa Kuppis cottage integration on the cottage Home
# Assistant. Run in the Terminal & SSH app of cottage-haos:
#
#   curl -fsSL https://raw.githubusercontent.com/Vkuparin/villa-kuppis/main/deploy/cottage/install.sh | sh -s -- --with-simulator
#
# Copies custom_components/villa_kuppis_cottage (always) and
# villa_kuppis_simulator (with --with-simulator) to /config/custom_components,
# adds the packages that are not there yet (an edited package is never
# overwritten), checks the configuration and restarts Home Assistant.
set -eu

REPO=https://github.com/Vkuparin/villa-kuppis/archive/refs/heads/main.tar.gz
CONFIG=${CONFIG:-/config}
SIMULATOR=0
RESTART=1
for arg in "$@"; do
  case "$arg" in
    --with-simulator) SIMULATOR=1 ;;
    --no-restart) RESTART=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -n "${SRC:-}" ]; then
  src=$SRC   # a local checkout, for testing
else
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  echo "Downloading Villa Kuppis (main)..."
  curl -fsSL "$REPO" | tar -xz -C "$work"
  src="$work/villa-kuppis-main"
fi

install_component() {
  rm -rf "$CONFIG/custom_components/$1.new"
  cp -r "$src/custom_components/$1" "$CONFIG/custom_components/$1.new"
  rm -rf "$CONFIG/custom_components/$1"
  mv "$CONFIG/custom_components/$1.new" "$CONFIG/custom_components/$1"
  echo "Installed custom_components/$1 ($(sed -n 's/.*"version": "\(.*\)".*/\1/p' "$CONFIG/custom_components/$1/manifest.json"))"
}

install_package() {
  if [ -e "$CONFIG/packages/$1" ]; then
    echo "Kept packages/$1 (already there)"
  else
    cp "$src/deploy/cottage/packages/$1" "$CONFIG/packages/$1"
    echo "Added packages/$1"
  fi
}

mkdir -p "$CONFIG/custom_components" "$CONFIG/packages"
install_component villa_kuppis_cottage
install_package villa_kuppis.yaml
if [ "$SIMULATOR" = 1 ]; then
  install_component villa_kuppis_simulator
  install_package villa_kuppis_simulator.yaml
fi

if grep -Eq '^[[:space:]]+packages:' "$CONFIG/configuration.yaml"; then
  echo "configuration.yaml already loads packages"
elif grep -Eq '^homeassistant:' "$CONFIG/configuration.yaml"; then
  cp "$CONFIG/configuration.yaml" "$CONFIG/configuration.yaml.before-villa-kuppis"
  sed -i 's/^homeassistant:.*$/homeassistant:\n  packages: !include_dir_named packages/' "$CONFIG/configuration.yaml"
  echo "Added packages to the homeassistant: section of configuration.yaml (backup: configuration.yaml.before-villa-kuppis)"
else
  cp "$CONFIG/configuration.yaml" "$CONFIG/configuration.yaml.before-villa-kuppis"
  printf '\nhomeassistant:\n  packages: !include_dir_named packages\n' >> "$CONFIG/configuration.yaml"
  echo "Added packages to configuration.yaml (backup: configuration.yaml.before-villa-kuppis)"
fi

if command -v ha >/dev/null 2>&1; then
  echo "Checking the configuration..."
  ha core check
  if [ "$RESTART" = 1 ]; then
    echo "Restarting Home Assistant..."
    ha core restart
  fi
else
  echo "No ha command here: check the configuration and restart Home Assistant from Settings."
fi
