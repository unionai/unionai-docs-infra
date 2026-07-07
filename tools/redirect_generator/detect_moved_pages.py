#!/usr/bin/env python3
"""
Detect moved/renamed pages from git history and generate redirect entries.

Usage:
    python detect_moved_pages.py [--dry-run]

Scans the full branch history for file renames under content/, generates
redirect entries for all variants, and appends new ones to redirects.csv.
Existing redirects are skipped (deduplicated by source URL).

Use git diff/restore to review or undo changes.
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _repo import get_repo_root, INFRA_ROOT

# CSV format flags (matching existing redirects.csv).
# Columns 2-6 (after source,target): status, include_subdomains,
# subpath_matching, preserve_query_string, preserve_path_suffix.
#
# Exact-match rows: subpath_matching=FALSE — the source URL is matched
# literally. The site serves canonical *trailing-slash* URLs, so for every
# exact-match rename we emit BOTH the no-slash and trailing-slash source forms
# (see generate_redirect_entries); otherwise the trailing-slash form 404s.
CSV_FLAGS = '302,TRUE,FALSE,TRUE,TRUE'

# Subtree-collapse rows: subpath_matching=TRUE + preserve_path_suffix=TRUE.
# A single rule on the moved prefix then covers the prefix itself, its
# trailing-slash form, and every deep subpath in one hop. Only emitted for a
# clean, fully-relocated subtree (see detect_subtree_moves' guards) — never
# blanket-flipped, or a leaf rule would "match" non-existent children.
COLLAPSE_FLAGS = '302,TRUE,TRUE,TRUE,TRUE'

# Default output file (in unionai-docs-infra/)
REDIRECTS_FILE = 'redirects.csv'

# Makefile include that defines VERSION
MAKEFILE_INC = 'makefile.inc'

# Hugo variant config file pattern: config.{variant}.toml
VARIANT_CONFIG_GLOB = 'config.*.toml'


def read_version(repo_path: Path) -> str:
    """Read VERSION from makefile.inc (e.g. 'v2' on main, 'v1' on v1 branch)."""
    inc_path = repo_path / MAKEFILE_INC
    for line in inc_path.read_text().splitlines():
        if line.startswith('VERSION'):
            # FORMAT: VERSION := v2
            return line.split(':=')[1].strip()
    print(f"Error: VERSION not found in {MAKEFILE_INC}", file=sys.stderr)
    sys.exit(1)


def read_variants(repo_path: Path) -> List[str]:
    """Read variant names from config.{variant}.toml files in unionai-docs-infra/."""
    variants = sorted(
        p.stem.split('.', 1)[1]
        for p in INFRA_ROOT.glob(VARIANT_CONFIG_GLOB)
    )
    if not variants:
        print(f"Error: no {VARIANT_CONFIG_GLOB} files found in {INFRA_ROOT}",
              file=sys.stderr)
        sys.exit(1)
    return variants


def run_git_command(args: List[str], cwd: Path, quiet: bool = False) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ['git'] + args,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        if not quiet:
            print(f"Git error: {result.stderr}", file=sys.stderr)
        return ""
    return result.stdout


def resolve_production_ref(repo_path: Path) -> str:
    """Resolve the production branch ref for the current version.

    Both 'main' (v2) and 'v1' are production branches. Determine which one
    to use based on the VERSION in makefile.inc, then resolve the ref.
    """
    version = read_version(repo_path)

    if version == 'v1':
        candidates = ['v1', 'origin/v1']
    else:
        candidates = ['main', 'origin/main']

    for ref in candidates:
        result = run_git_command(['rev-parse', '--verify', ref], repo_path, quiet=True)
        if result.strip():
            return ref

    print(f"Error: no production branch ref found for version {version} "
          f"(tried {', '.join(candidates)})", file=sys.stderr)
    sys.exit(1)


def get_published_files(repo_path: Path) -> Set[str]:
    """Get all content files that have ever existed on the production branch."""
    prod_ref = resolve_production_ref(repo_path)
    # Files currently on the production branch
    current = run_git_command(
        ['ls-tree', '-r', '--name-only', prod_ref, '--', 'content/'],
        repo_path
    )
    # Files deleted on the production branch (existed before but were removed)
    deleted = run_git_command(
        ['log', prod_ref, '--diff-filter=D', '--name-only', '--format=', '--', 'content/'],
        repo_path
    )
    published = set()
    for output in [current, deleted]:
        for line in output.strip().split('\n'):
            line = line.strip()
            if line and line.endswith('.md'):
                published.add(line)
    return published


def detect_renames(repo_path: Path) -> List[Tuple[str, str]]:
    """Detect file renames in git history.

    Returns list of (old_path, new_path) tuples.
    """
    args = ['log', '--diff-filter=R', '-M', '--name-status', '--format=',
            '--', 'content/']

    output = run_git_command(args, repo_path)

    renames = []
    for line in output.strip().split('\n'):
        if not line or not line.startswith('R'):
            continue

        # Format: R<similarity>\t<old_path>\t<new_path>
        parts = line.split('\t')
        if len(parts) >= 3:
            old_path = parts[1]
            new_path = parts[2]
            renames.append((old_path, new_path))

    return renames


def content_path_to_url(content_path: str, variant: str, version: str) -> str:
    """Convert a content path to a URL path.

    Examples (version='v2'):
        content/user-guide/foo.md -> www.union.ai/docs/v2/{variant}/user-guide/foo
        content/user-guide/bar/_index.md -> www.union.ai/docs/v2/{variant}/user-guide/bar
    """
    # Remove content/ prefix
    path = content_path
    if path.startswith('content/'):
        path = path[len('content/'):]

    # Remove .md extension
    if path.endswith('.md'):
        path = path[:-3]

    # Handle _index files (directory index pages)
    if path.endswith('/_index'):
        path = path[:-7]  # Remove /_index
    elif path.endswith('/index'):
        path = path[:-6]  # Remove /index

    # Build URL
    return f"www.union.ai/docs/{version}/{variant}/{path}"


def load_existing_redirects(csv_path: Path) -> Dict[str, str]:
    """Load existing redirects as source -> destination map."""
    existing = {}

    if not csv_path.exists():
        return existing

    with open(csv_path, 'r', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                existing[row[0]] = row[1]

    return existing


def _emit_entry(
    source_url: str,
    target_url: str,
    flags: str,
    existing: Dict[str, str],
    out: List[str],
) -> bool:
    """Append one CSV row for source_url unless it already exists.

    Dedups against `existing` (source -> destination map): if source_url is
    already present with the same destination it is silently kept; with a
    different destination a warning is printed. Either way nothing is emitted.
    Returns True iff a new row was appended to `out`.
    """
    if source_url in existing:
        existing_dest = existing[source_url]
        if existing_dest == target_url:
            print(f"  [skip] redirect already exists: {source_url}")
        else:
            print(f"  [skip] redirect exists with different destination: {source_url}")
            print(f"         existing:  {existing_dest}")
            print(f"         expected:  {target_url}")
        return False

    out.append(f"{source_url},{target_url},{flags}")
    return True


def generate_redirect_entries(
    renames: List[Tuple[str, str]],
    existing: Dict[str, str],
    version: str,
    variants: List[str]
) -> List[str]:
    """Generate exact-match redirect entries for all variants.

    For every rename we emit BOTH source forms — the no-slash URL and its
    trailing-slash variant — pointing at the same (no-slash) target with the
    same flags. The site serves canonical trailing-slash URLs, so emitting only
    the no-slash source leaves bookmarked trailing-slash URLs 404ing (DOC-1233).

    A rename is fully skipped only when BOTH forms already exist in `existing`;
    if just one is present the missing form is still emitted.
    """
    new_entries: List[str] = []

    for old_path, new_path in renames:
        for variant in variants:
            old_url = content_path_to_url(old_path, variant, version)
            new_url = content_path_to_url(new_path, variant, version)

            # Skip self-redirects (rename resolved to same URL).
            # Case-insensitive comparison because Hugo lowercases all URL paths
            # by default, so case-only renames don't change the published URL.
            if old_url.lower() == new_url.lower():
                continue

            target = f"https://{new_url}"
            # Both source forms, same target/flags (target keeps no trailing
            # slash). Per-form dedup means "skip only if BOTH already exist".
            _emit_entry(old_url, target, CSV_FLAGS, existing, new_entries)
            _emit_entry(old_url + '/', target, CSV_FLAGS, existing, new_entries)

    return new_entries


def _is_under(path: str, prefix_components: List[str]) -> bool:
    """True if `path` (slash-joined) sits strictly under prefix_components/."""
    pc = path.split('/')
    return (
        len(pc) > len(prefix_components)
        and pc[:len(prefix_components)] == prefix_components
    )


def _trailing_run_length(old_comps: List[str], new_comps: List[str]) -> int:
    """Length of the longest equal *trailing* run of path components.

    This is the preserved relative suffix of a rename; the components before it
    are the diverging prefix. 0 means the basename (or suffix) itself changed,
    so the rename is not a pure relocation.
    """
    n = 0
    limit = min(len(old_comps), len(new_comps))
    while n < limit and old_comps[-(n + 1)] == new_comps[-(n + 1)]:
        n += 1
    return n


def detect_subtree_moves(
    renames: List[Tuple[str, str]],
    existing_paths: Set[str],
    published_files: Set[str],
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Detect clean full-subtree relocations to collapse into one subpath rule.

    Args:
        renames: post-filter (old, new) content paths (published, still-served
            already removed). This is the "full set" the guards reason about.
        existing_paths: lowercased posix paths *relative to content/* that exist
            in the current content tree (the live, still-served pages).
        published_files: every content path ever published on the production
            branch (current + ever-deleted), e.g. 'content/foo/bar.md'.

    Returns:
        (collapse_moves, remaining_renames) where collapse_moves is a list of
        (old_prefix_path, new_prefix_path) content-path prefixes that each
        qualify to collapse into a single subpath_matching rule, and
        remaining_renames is every rename NOT consumed by a collapse (left to
        the per-page exact-match path).

    A cluster keyed on the shallowest divergence (old_prefix -> new_prefix)
    collapses ONLY if ALL guards hold:
      * size      >= 2 renames.
      * uniform   every rename whose old path is under old_prefix/ is in this
                  cluster (no page under old_prefix maps to a different prefix).
      * survivors NO .md exists under old_prefix/ in the current content tree
                  (safety-critical: never clobber a live page).
      * complete  every historically-published file under old_prefix/ appears
                  as an old in this cluster (nothing left behind / deleted
                  without redirect / moved elsewhere undetected).
    Conservative: only the shallowest prefix pair is considered; if it fails any
    guard its renames fall back to per-page (we do NOT try deeper sub-prefixes).
    """
    # Cluster renames by (old_prefix, new_prefix) = components before the
    # preserved trailing suffix.
    clusters: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], List[Tuple[str, str]]] = {}
    for old, new in renames:
        oc = old.split('/')
        nc = new.split('/')
        run = _trailing_run_length(oc, nc)
        if run == 0:
            # Basename/suffix changed → not relocatable; leave to per-page.
            continue
        old_prefix = tuple(oc[:-run])
        new_prefix = tuple(nc[:-run])
        # Require a real path component beyond the 'content' root on the source
        # side; collapsing at the content root itself would be absurd/unsafe.
        if len(old_prefix) < 2 or len(new_prefix) < 1:
            continue
        clusters.setdefault((old_prefix, new_prefix), []).append((old, new))

    collapse_moves: List[Tuple[str, str]] = []
    consumed: Set[Tuple[str, str]] = set()

    for (old_prefix, new_prefix), members in clusters.items():
        prefix_comps = list(old_prefix)

        # (size)
        if len(members) < 2:
            continue

        # (uniform / no fan-out) — checked against the FULL renames set.
        renames_under = [(o, n) for (o, n) in renames if _is_under(o, prefix_comps)]
        if len(renames_under) != len(members):
            continue

        # (no survivors) — EXACT, safety-critical. existing_paths is relative to
        # content/ and lowercased; old_prefix includes the leading 'content'.
        rel_prefix = '/'.join(old_prefix[1:]).lower()
        if any(ep == rel_prefix or ep.startswith(rel_prefix + '/') for ep in existing_paths):
            continue

        # (completeness)
        cluster_olds = {o for (o, _) in members}
        if any(
            p not in cluster_olds
            for p in published_files
            if _is_under(p, prefix_comps)
        ):
            continue

        # Qualifies.
        collapse_moves.append(('/'.join(old_prefix), '/'.join(new_prefix)))
        consumed.update(members)

    remaining = [(o, n) for (o, n) in renames if (o, n) not in consumed]
    return collapse_moves, remaining


def generate_collapse_entries(
    collapse_moves: List[Tuple[str, str]],
    existing: Dict[str, str],
    version: str,
    variants: List[str],
) -> List[str]:
    """Generate one subpath_matching redirect per variant per collapsed subtree.

    Source = URL(old_prefix), target = https://URL(new_prefix), flags
    COLLAPSE_FLAGS. subpath_matching + preserve_path_suffix make this single
    rule cover the prefix, its trailing-slash form, and all deep subpaths — so
    (unlike the exact-match path) no second slash form is needed.
    """
    entries: List[str] = []

    for old_prefix_path, new_prefix_path in collapse_moves:
        for variant in variants:
            old_url = content_path_to_url(old_prefix_path, variant, version)
            new_url = content_path_to_url(new_prefix_path, variant, version)

            if old_url.lower() == new_url.lower():
                continue

            target = f"https://{new_url}"
            _emit_entry(old_url, target, COLLAPSE_FLAGS, existing, entries)

    return entries


def collapse_chains(csv_path: Path) -> int:
    """Collapse multi-hop redirect chains so every source points to the final destination.

    Returns the number of redirects updated.
    """
    # Parse all rows, building a source -> (dest, rest_of_fields) map
    rows: List[List[str]] = []
    source_to_dest: Dict[str, str] = {}

    with open(csv_path, 'r', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
            if len(row) >= 2:
                source_to_dest[row[0]] = row[1]

    # For each redirect, follow the chain to the terminal destination.
    # The dest URL in the CSV has https:// prefix, but source URLs don't,
    # so we need to strip https:// from dest to look it up as a source.
    updated = 0
    for row in rows:
        if len(row) < 2:
            continue
        dest = row[1]
        # Follow the chain
        seen = {row[0]}  # track visited to detect cycles
        while True:
            # Strip https:// to match source URL format
            dest_as_source = dest.removeprefix('https://')
            if dest_as_source not in source_to_dest:
                break
            if dest_as_source in seen:
                print(f"  [warn] redirect cycle detected involving: {dest_as_source}",
                      file=sys.stderr)
                break
            seen.add(dest_as_source)
            dest = source_to_dest[dest_as_source]
        if dest != row[1]:
            row[1] = dest
            updated += 1

    # Remove self-redirects (source == destination)
    filtered = []
    removed = 0
    for row in rows:
        if len(row) >= 2 and row[0] == row[1].removeprefix('https://'):
            removed += 1
        else:
            filtered.append(row)

    if removed:
        print(f"  Removed {removed} self-redirects")

    if updated or removed:
        with open(csv_path, 'w', newline='') as f:
            for row in filtered:
                f.write(','.join(row) + '\n')

    return updated


def main():
    parser = argparse.ArgumentParser(
        description='Detect moved pages and generate redirect entries'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print new redirects without modifying the file'
    )
    parser.add_argument(
        '--output',
        default=REDIRECTS_FILE,
        help=f'Output CSV file (default: {REDIRECTS_FILE})'
    )
    args = parser.parse_args()

    repo_path = get_repo_root()
    output_path = INFRA_ROOT / args.output
    version = read_version(repo_path)
    variants = read_variants(repo_path)

    print(f"Detecting file renames in git history...")
    renames = detect_renames(repo_path)
    print(f"  Found {len(renames)} renamed files")

    # Build new redirect entries from the detected renames. Any of the filters
    # below may legitimately leave zero new entries (e.g. every rename already
    # has a redirect) — that is fine. On a real run we still fall through to the
    # chain-collapse pass, which must run on EVERY invocation, not only when new
    # entries happen to be appended: a chain can be introduced by a manual CSV
    # edit or a delete+add page move that git does not score as a rename, and
    # such chains would otherwise never be collapsed (DOC-1190).
    new_entries = []
    if renames:
        # Filter to only renames where the source was previously published
        published_files = get_published_files(repo_path)
        unpublished_renames = [(o, n) for o, n in renames if o not in published_files]
        renames = [(o, n) for o, n in renames if o in published_files]
        if unpublished_renames:
            print(f"  Skipping {len(unpublished_renames)} renames of files never published on {resolve_production_ref(repo_path)}")

        # Skip renames where the source URL is still served (e.g. foo.md -> foo/_index.md).
        # Use case-insensitive matching since Hugo lowercases all URLs.
        content_dir = repo_path / 'content'
        existing_paths = {
            p.relative_to(content_dir).as_posix().lower()
            for p in content_dir.rglob('*.md')
        }
        preserved = []
        for old_path, new_path in renames:
            rel = old_path.removeprefix('content/').lower()
            stem = rel.removesuffix('.md')
            if rel in existing_paths or f"{stem}/_index.md" in existing_paths:
                preserved.append((old_path, new_path))
        if preserved:
            print(f"  Skipping {len(preserved)} renames where source URL is still served")
            renames = [(o, n) for o, n in renames if (o, n) not in preserved]

        if renames:
            print(f"Loading existing redirects from {args.output}...")
            existing = load_existing_redirects(output_path)
            print(f"  Found {len(existing)} existing redirects")

            # Collapse any clean full-subtree relocations into single subpath rules,
            # leaving scattered single-page moves to the per-page exact-match path.
            collapse_moves, renames = detect_subtree_moves(
                renames, existing_paths, published_files
            )
            if collapse_moves:
                print(f"  Collapsing {len(collapse_moves)} subtree move(s) into subpath rules:")
                for old_prefix, new_prefix in collapse_moves:
                    print(f"    {old_prefix}/  ->  {new_prefix}/")

            print(f"Generating redirect entries for variants: {', '.join(variants)} (version: {version})...")
            new_entries = generate_collapse_entries(collapse_moves, existing, version, variants)
            new_entries += generate_redirect_entries(renames, existing, version, variants)

    if args.dry_run:
        # Dry run reports only the new entries it would append; it never mutates
        # the file, so it does not run the (mutating) chain-collapse pass.
        if new_entries:
            print(f"  Generated {len(new_entries)} new redirect entries")
            print("\nNew entries (dry run):\n")
            for entry in new_entries:
                print(entry)
        else:
            print("No new redirect entries (renames already covered)")
        return 0

    # Append any new entries to redirects.csv.
    if new_entries:
        print(f"  Generated {len(new_entries)} new redirect entries")
        print(f"Appending to {args.output}...")
        with open(output_path, 'a', newline='') as f:
            # Ensure file ends with newline before appending
            if output_path.stat().st_size > 0:
                with open(output_path, 'rb') as rb:
                    rb.seek(-1, 2)
                    if rb.read(1) != b'\n':
                        f.write('\n')
            for entry in new_entries:
                f.write(entry + '\n')
        print(f"Added {len(new_entries)} new redirects to {args.output}")
    else:
        print("No new redirect entries (renames already covered)")

    # Always collapse multi-hop redirect chains, even when no new entries were
    # added (DOC-1190). This keeps the CSV single-hop after a manual edit or a
    # non-rename page move; the chain pytest gates it in CI.
    print(f"Collapsing redirect chains...")
    collapsed = collapse_chains(output_path)
    if collapsed:
        print(f"  Updated {collapsed} redirects to point to final destination")
    else:
        print(f"  No chains found")

    return 0


if __name__ == '__main__':
    sys.exit(main())
