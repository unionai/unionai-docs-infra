#!/usr/bin/env bash
set -euo pipefail

# Generate Helm chart reference docs from unionai/helm-charts.
#
# Usage:
#   generate_helm_docs.sh [path-to-helm-charts-repo]
#
# If no path is provided, auto-clones unionai/helm-charts to a temp directory.
# If helm-docs is not on PATH, auto-downloads it to a temp directory.

HELM_DOCS_VERSION=1.14.2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# REPO_ROOT is set by the Makefile; fall back to git discovery
DOCS_ROOT="${REPO_ROOT:-$(git -C "$INFRA_ROOT" rev-parse --show-toplevel 2>/dev/null || cd "$INFRA_ROOT/.." && pwd)}"

CHARTS=(dataplane knative-operator)
TEMPLATE_DIR="$DOCS_ROOT/scripts/helm-docs"
# Keep in step with CONTENT_DIR in check_helm_versions.py.
DEST_DIR="$DOCS_ROOT/content/deployment/selfmanaged/helm-chart-reference"
CLEANUP_FILES=()
CLEANUP_DIRS=()

# --- Ensure helm-docs is available ---

if command -v helm-docs &>/dev/null; then
  HELM_DOCS=helm-docs
else
  # Auto-download to temp directory
  HELM_DOCS_DIR="$(mktemp -d)"
  CLEANUP_DIRS+=("$HELM_DOCS_DIR")
  OS="$(uname -s)"    # Darwin or Linux
  ARCH="$(uname -m)"  # arm64 or x86_64
  # helm-docs uses "x86_64" and "arm64" in release names
  TARBALL="helm-docs_${HELM_DOCS_VERSION}_${OS}_${ARCH}.tar.gz"
  URL="https://github.com/norwoodj/helm-docs/releases/download/v${HELM_DOCS_VERSION}/${TARBALL}"
  echo "Downloading helm-docs v${HELM_DOCS_VERSION} for ${OS}/${ARCH}..."
  curl -sSL "$URL" | tar xz -C "$HELM_DOCS_DIR" helm-docs
  HELM_DOCS="$HELM_DOCS_DIR/helm-docs"
fi

# --- Locate or clone helm-charts repo ---

if [[ -n "${1:-}" ]]; then
  HELM_CHARTS_DIR="$(cd "$1" && pwd)"
else
  # Auto-clone to temp directory
  HELM_CHARTS_DIR="$(mktemp -d)"
  CLEANUP_DIRS+=("$HELM_CHARTS_DIR")
  echo "Cloning unionai/helm-charts (shallow)..."
  git clone --depth 1 --quiet https://github.com/unionai/helm-charts.git "$HELM_CHARTS_DIR"
fi

# --- Cleanup handler ---

cleanup() {
  for f in "${CLEANUP_FILES[@]}"; do
    rm -f "$f"
  done
  for d in "${CLEANUP_DIRS[@]}"; do
    rm -rf "$d"
  done
}
trap cleanup EXIT

# Refuse to write outside the published section. If the section moves, fail
# here rather than writing pages to a directory nothing links to.
if [[ ! -f "$DEST_DIR/_index.md" ]]; then
  echo "ERROR: $DEST_DIR/_index.md not found. Has the Helm chart reference section moved?"
  echo "       Update DEST_DIR here and CONTENT_DIR in check_helm_versions.py."
  exit 1
fi

# --- Generate docs for each chart ---

for CHART in "${CHARTS[@]}"; do
  CHART_DIR="$HELM_CHARTS_DIR/charts/$CHART"

  if [[ ! -d "$CHART_DIR" ]]; then
    echo "ERROR: Chart directory not found: $CHART_DIR"
    exit 1
  fi

  TEMPLATE_SRC="$TEMPLATE_DIR/$CHART.md.gotmpl"
  TEMPLATE_DST="$CHART_DIR/$CHART.md.gotmpl"
  OUTPUT="$CHART_DIR/README.md"
  DEST="$DEST_DIR/$CHART.md"

  # Copy template into chart directory (helm-docs expects it there)
  cp "$TEMPLATE_SRC" "$TEMPLATE_DST"
  CLEANUP_FILES+=("$TEMPLATE_DST")

  echo "Generating helm-docs for $CHART..."
  "$HELM_DOCS" \
    --chart-search-root "$CHART_DIR" \
    --template-files "$CHART.md.gotmpl" \
    --output-file README.md

  # Wrap angle-bracket placeholders (e.g. <ACCOUNT_ID>, <configmap-name>) in
  # backticks so Hugo's goldmark renderer doesn't treat them as raw HTML.
  # Pattern: < followed by a letter, then 1+ letters/digits/underscores/hyphens, then >
  # Minimum 2 chars inside brackets to avoid matching real HTML tags like <p>, <a>.
  # Code spans are left alone: a placeholder already inside one (e.g.
  # `*.apps.<dp>.<domain>`) is safe, and wrapping it again splits the span into
  # broken fragments.
  # On table rows, a bare | inside a code span is escaped as \|: GFM splits
  # cells on it even inside backticks, so a default such as
  # `(.Values.provider | default "")` would otherwise cut the row short.
  perl -pe '
    my $row = /^\|/;
    s{(`[^`]*`)|<([A-Za-z][A-Za-z0-9_-]+)>}{
      if (defined $1) { my $c = $1; $c =~ s/(?<!\\)\|/\\|/g if $row; $c }
      else { "`<$2>`" }
    }ge' "$OUTPUT" > "$DEST"
  echo "Generated: $DEST"
done
