#!/bin/bash

# The Hugo version this repo builds with, read from .hugoversion so there is ONE
# number to change. CI pins the same value (see README "Toolchain"); a mismatch
# between the two is itself a defect worth catching.
#
# The floor USED to be 0.145.0 while the templates already called hugo.Data,
# which needs >= 0.156 -- so the declared constraint was wrong for eleven minor
# versions and was papered over with a .Site.Data fallback branch (infra#195)
# rather than corrected. Floor now equals the pin: local dev and CI run the same
# Hugo, and version-conditional template code is unnecessary.
_here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
declare -r hugo_pin_version=$(cat "${_here}/.hugoversion" 2>/dev/null || echo "0.161.1")
declare -r hugo_min_version="${hugo_pin_version}"

ver_from_string() {
  echo "$@" | awk -F. '{ printf("%d%03d%03d%03d\n", $1,$2,$3,$4); }';
}

if ! command -v hugo 1>/dev/null; then
  cat <<EOF
---------------------------------------
FATAL: 'hugo' site builder required.

Install with:
  MacOS  : brew install hugo
  Ubuntu : apt  install hugo
---------------------------------------

EOF
  exit 1
fi

hugo_ver=$(hugo version | grep -o 'v[0-9]\+\.[0-9]\+\.[0-9]\+' | sed 's/v//')
version_number=$(ver_from_string $hugo_ver)
min_version_number=$(ver_from_string $hugo_min_version)


if [[ "$version_number" -lt "$min_version_number" ]]; then
  cat <<EOF
---------------------------------------
FATAL: 'hugo' version ${hugo_min_version} or greater required.
       Found ${hugo_ver}.

Install with:
  MacOS  : brew install hugo
  Ubuntu : apt  install hugo
---------------------------------------
EOF
  exit 1
fi

# Running AHEAD of the pin is the likelier skew in practice -- `brew install hugo`
# tracks latest, so a contributor is typically newer than CI, not older. A floor
# cannot catch that, and it is the more dangerous direction: it renders fine
# locally and can differ in the build that reaches readers (and makes a
# check-determinism failure unreproducible on your machine). Warn, do not fail --
# being newer is usually harmless, and blocking every `make dev` on a Hugo release
# would be worse than the risk.
if [[ "$version_number" -gt "$min_version_number" ]]; then
  cat >&2 <<EOF
---------------------------------------
WARNING: local Hugo ${hugo_ver} is NEWER than the pinned build version ${hugo_pin_version}.

Output may differ from CI. If you are chasing a CI-only build or determinism
failure, match the pin before trusting a local result.
---------------------------------------
EOF
fi
