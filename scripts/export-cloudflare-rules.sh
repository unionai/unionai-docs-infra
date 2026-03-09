#!/usr/bin/env bash
#
# Export Cloudflare and CloudFront configuration in machine-readable JSON.
#
# Usage:
#   export CF_TOKEN="<Cloudflare API token with read access to all resources>"
#   ./scripts/export-cloudflare-rules.sh
#
# Optional: If AWS CLI is configured, also exports CloudFront distribution configs.
#   aws configure  # (if not already done)
#
# Output: writes JSON files to ./cloudflare-export/

set -euo pipefail

OUTDIR="./cloudflare-export"
mkdir -p "$OUTDIR"

CF_API="https://api.cloudflare.com/client/v4"

if [ -z "${CF_TOKEN:-}" ]; then
  echo "Error: Set CF_TOKEN environment variable."
  exit 1
fi

cf_fetch() {
  local endpoint="$1"
  curl -sf -H "Authorization: Bearer $CF_TOKEN" "$CF_API$endpoint"
}

# --- Helper: get rulesets for a zone ---
get_zone_rulesets() {
  local zone_id="$1"

  local rulesets
  rulesets=$(cf_fetch "/zones/$zone_id/rulesets" | jq '.result // []')

  local count
  count=$(echo "$rulesets" | jq 'length')

  if [ "$count" -eq 0 ]; then
    echo "[]"
    return
  fi

  local details="[]"
  for rs_id in $(echo "$rulesets" | jq -r '.[].id'); do
    local rs
    rs=$(cf_fetch "/zones/$zone_id/rulesets/$rs_id" | jq '.result // empty')
    if [ -n "$rs" ]; then
      details=$(echo "$details" | jq --argjson rs "$rs" '. + [$rs]')
    fi
  done
  echo "$details"
}

# --- Helper: get legacy Page Rules for a zone ---
get_page_rules() {
  local zone_id="$1"
  cf_fetch "/zones/$zone_id/pagerules?per_page=100" | jq '.result // []'
}

# --- Helper: get DNS records for a zone ---
get_dns_records() {
  local zone_id="$1"
  local all_records="[]"
  local page=1

  while true; do
    local resp
    resp=$(cf_fetch "/zones/$zone_id/dns_records?per_page=100&page=$page")
    local records
    records=$(echo "$resp" | jq '.result // []')
    local count
    count=$(echo "$records" | jq 'length')

    if [ "$count" -eq 0 ]; then
      break
    fi

    all_records=$(echo "$all_records" | jq --argjson r "$records" '. + $r')
    page=$((page + 1))

    # Check if there are more pages
    local total_pages
    total_pages=$(echo "$resp" | jq '.result_info.total_pages // 1')
    if [ "$page" -gt "$total_pages" ]; then
      break
    fi
  done

  echo "$all_records"
}

# --- Helper: get Workers routes for a zone ---
get_workers_routes() {
  local zone_id="$1"
  cf_fetch "/zones/$zone_id/workers/routes" | jq '.result // []'
}

# --- Helper: get Bulk Redirect Lists for an account (with pagination) ---
# Writes result to $1 (output file path)
get_bulk_redirect_lists() {
  local account_id="$1"
  local outfile="$2"
  local tmpdir
  tmpdir=$(mktemp -d)

  local lists
  lists=$(cf_fetch "/accounts/$account_id/rules/lists?per_page=50" | jq '[.result[] | select(.kind == "redirect")]')

  local count
  count=$(echo "$lists" | jq 'length')

  if [ "$count" -eq 0 ]; then
    echo "[]" > "$outfile"
    rm -rf "$tmpdir"
    return
  fi

  local list_index=0
  for list_id in $(echo "$lists" | jq -r '.[].id'); do
    local list_name
    list_name=$(echo "$lists" | jq -r --arg id "$list_id" '.[] | select(.id == $id) | .name')

    # Paginate through all items, writing each page to a temp file
    local cursor=""
    local page=0
    while true; do
      local url="/accounts/$account_id/rules/lists/$list_id/items?per_page=500"
      if [ -n "$cursor" ]; then
        url="$url&cursor=$cursor"
      fi
      local resp
      resp=$(cf_fetch "$url")
      echo "$resp" | jq '.result // []' > "$tmpdir/page_${page}.json"
      local item_count
      item_count=$(jq 'length' "$tmpdir/page_${page}.json")

      if [ "$item_count" -eq 0 ]; then
        rm -f "$tmpdir/page_${page}.json"
        break
      fi

      page=$((page + 1))
      cursor=$(echo "$resp" | jq -r '.result_info.cursors.after // empty')

      if [ -z "$cursor" ]; then
        break
      fi
    done

    # Merge all pages into one array using jq slurp on files
    local items_file="$tmpdir/list_${list_index}_items.json"
    if ls "$tmpdir"/page_*.json &>/dev/null; then
      jq -s 'add' "$tmpdir"/page_*.json > "$items_file"
      rm -f "$tmpdir"/page_*.json
    else
      echo "[]" > "$items_file"
    fi

    local total_items
    total_items=$(jq 'length' "$items_file")
    echo "    List '$list_name': $total_items items" >&2

    # Write list metadata
    jq -n --arg name "$list_name" --arg id "$list_id" '{name: $name, id: $id}' > "$tmpdir/list_${list_index}_meta.json"

    list_index=$((list_index + 1))
  done

  # Assemble final output: array of {name, id, items} objects
  local final="[]"
  for i in $(seq 0 $((list_index - 1))); do
    # Combine meta + items into one object, using files to avoid arg-too-long
    jq --slurpfile items "$tmpdir/list_${i}_items.json" '. + {items: $items[0]}' "$tmpdir/list_${i}_meta.json" > "$tmpdir/list_${i}_combined.json"
    final=$(jq --slurpfile entry "$tmpdir/list_${i}_combined.json" '. + $entry' <<< "$final")
  done

  echo "$final" | jq . > "$outfile"
  rm -rf "$tmpdir"
}

# --- Helper: get Pages projects for an account ---
get_pages_projects() {
  local account_id="$1"
  cf_fetch "/accounts/$account_id/pages/projects" | jq '[.result[] | {name, subdomain, domains, production_branch, created_on, latest_deployment: (.latest_deployment | {url, environment, created_on, aliases} // null)}]'
}

# ============================================================
# Cloudflare Export
# ============================================================

echo "=== Discovering Cloudflare zones ==="
all_zones=$(cf_fetch "/zones?per_page=50" | jq '[.result[] | {id, name, account_id: .account.id, account_name: .account.name}]')
echo "$all_zones" | jq -r '.[] | "  \(.account_name) / \(.name)"'

# Track account IDs we've already processed for account-level resources
seen_accounts=""

for row in $(echo "$all_zones" | jq -r '.[] | @base64'); do
  zone_id=$(echo "$row" | base64 -d | jq -r '.id')
  zone_name=$(echo "$row" | base64 -d | jq -r '.name')
  account_id=$(echo "$row" | base64 -d | jq -r '.account_id')
  account_name=$(echo "$row" | base64 -d | jq -r '.account_name')
  safe_account=$(echo "$account_name" | tr ' .' '-' | tr '[:upper:]' '[:lower:]')
  safe_zone=$(echo "$zone_name" | tr '.' '-')

  echo ""
  echo "--- $account_name / $zone_name ---"

  echo "  Fetching rulesets..."
  get_zone_rulesets "$zone_id" | jq . > "$OUTDIR/${safe_account}_${safe_zone}_rulesets.json"

  echo "  Fetching page rules..."
  get_page_rules "$zone_id" | jq . > "$OUTDIR/${safe_account}_${safe_zone}_pagerules.json"

  echo "  Fetching DNS records..."
  get_dns_records "$zone_id" | jq . > "$OUTDIR/${safe_account}_${safe_zone}_dns.json"

  echo "  Fetching Workers routes..."
  get_workers_routes "$zone_id" | jq . > "$OUTDIR/${safe_account}_${safe_zone}_workers-routes.json"

  # Account-level resources (once per account)
  if ! echo "$seen_accounts" | grep -q "$account_id"; then
    seen_accounts="$seen_accounts $account_id"

    echo "  Fetching bulk redirect lists for account..."
    get_bulk_redirect_lists "$account_id" "$OUTDIR/${safe_account}_bulk-redirects.json"

    echo "  Fetching Pages projects for account..."
    get_pages_projects "$account_id" | jq . > "$OUTDIR/${safe_account}_pages-projects.json"
  fi
done

# ============================================================
# CloudFront Export
# ============================================================

echo ""
echo "=== CloudFront Export ==="

if command -v aws &>/dev/null; then
  # Check if AWS CLI is configured
  if aws sts get-caller-identity &>/dev/null 2>&1; then
    echo "  Listing CloudFront distributions..."
    distributions=$(aws cloudfront list-distributions --output json 2>/dev/null)

    if [ -n "$distributions" ]; then
      # Save the full list
      echo "$distributions" | jq . > "$OUTDIR/cloudfront_distributions-list.json"

      # Get each distribution's full config
      dist_ids=$(echo "$distributions" | jq -r '.DistributionList.Items[]?.Id // empty')
      for dist_id in $dist_ids; do
        aliases=$(echo "$distributions" | jq -r --arg id "$dist_id" '.DistributionList.Items[] | select(.Id == $id) | [.Aliases.Items[]?] | join(", ")')
        echo "  Fetching config for $dist_id ($aliases)..."
        aws cloudfront get-distribution-config --id "$dist_id" --output json > "$OUTDIR/cloudfront_${dist_id}_config.json"
      done
    else
      echo "  No CloudFront distributions found."
    fi
  else
    echo "  AWS CLI not configured (aws sts get-caller-identity failed). Skipping CloudFront."
    echo "  Run 'aws configure' to set up credentials."
  fi
else
  echo "  AWS CLI not installed. Skipping CloudFront."
  echo "  Install with: brew install awscli"
fi

# ============================================================
# Summary
# ============================================================

echo ""
echo "=== Done ==="
echo "Output files in $OUTDIR/:"
ls -1 "$OUTDIR/"
