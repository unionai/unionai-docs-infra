#!/usr/bin/env python3
"""
Simple script to build consolidated LLM-optimized documents by following ## Subpages links
in depth-first order starting from md/index.md.

Usage: python build_llm_docs.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from page_paths import (  # noqa: E402  (path shim must precede the import)
    PAGE_SUFFIX,
    is_section_landing,
    iter_page_twins,
    iter_pages,
    page_for,
    root_page,
    source_dir_of,
    url_dir_of,
)


# Cap on a size-abridged child's excerpt. Long enough to say what the page is
# about, short enough that abridging actually buys something.
EXCERPT_MAX_CHARS = 320

# One `## Subpages` entry, as Hugo writes it in `layouts/_default/list.md`:
#
#     - [Title](url)
#     - [Title](url) - the child's front-matter description
#
# Anchored to the start of a line, so a markdown link sitting INSIDE a
# description is not read as a second entry. Everything after the closing paren
# is captured as one tail that runs to end of line, so a description survives
# whatever it contains -- brackets, parens, a link, a further ` - `.
# One markdown link. Shared by the absolutizer and the heading rewrite so the
# two cannot drift; a heading whose link the pattern misses is copied verbatim
# and resolves against the wrong page.
MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

SUBPAGE_ENTRY_RE = re.compile(
    r'^- \[([^\]]+)\]\(([^)]+)\)(?P<tail>.*)$', re.MULTILINE)


def subpage_description(tail: str) -> str:
    """The description carried by a `## Subpages` entry's tail, or ''.

    The template writes exactly ` - <description>`; anything else after the
    link is not a description and is dropped, which is what happened to every
    tail before DOC-1508.
    """
    return tail[3:].strip() if tail.startswith(' - ') else ''


class LLMDocBuilder:
    def __init__(self, base_path: Path, quiet: bool = False):
        self.base_path = base_path
        self.quiet = quiet
        self.visited_files: Set[str] = set()
        self.title_lookup: dict[str, str] = {}  # Maps file paths to hierarchical titles
        self.version = self._detect_version()
        self.resolution_issues: List[dict] = []  # Track failed link resolutions
        self.current_source_file: str = ""  # Track current file being processed
        self.variant_root: Path = Path()  # Set per-variant in build_consolidated_doc
        # Hugo sources. The leaf/section discriminator lives here, not in the
        # output tree, which cannot tell the two apart -- see page_paths.py.
        self.content_root: Path = base_path / 'content'
        self.index_entries: List[tuple] = []  # (hierarchical_title, page_url, path_key) for index
        self.page_headings: dict[str, List[str]] = {}  # path_key -> [H2/H3 heading titles]
        self.section_pages: set[str] = set()  # path_keys of pages that have subpages

    def _detect_version(self) -> str:
        """Detect version from environment or makefile.inc."""
        # Check environment variable first (set by Makefile)
        version = os.environ.get('VERSION')
        if version:
            return version

        # Read from makefile.inc as fallback
        makefile_inc = self.base_path / 'makefile.inc'
        if makefile_inc.exists():
            try:
                with open(makefile_inc, 'r') as f:
                    for line in f:
                        if line.startswith('VERSION :='):
                            return line.split(':=')[1].strip()
            except Exception:
                pass

        # Default fallback
        return 'v2'

    def run_make_dist(self) -> bool:
        """Run make dist to regenerate all documentation variants."""
        if not self.quiet:
            print("Running 'make dist' to regenerate documentation...")
        try:
            result = subprocess.run(['make', 'dist'],
                                  cwd=self.base_path,
                                  capture_output=True,
                                  text=True,
                                  timeout=300)
            if result.returncode == 0:
                if not self.quiet:
                    print("Successfully regenerated documentation")
                return True
            else:
                print(f"Error: Make dist failed with return code {result.returncode}")
                return False
        except Exception as e:
            print(f"Error running make dist: {e}")
            return False

    def read_file_content(self, file_path: Path) -> str:
        """Read and clean markdown file content."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Transform source file references to GitHub URLs
            def transform_source_ref(match):
                source_line = match.group(0)
                # Replace /unionai-examples with GitHub URL
                transformed = source_line.replace('/unionai-examples', 'https://github.com/unionai/unionai-examples/blob/main')
                # Remove all asterisks and make it more explicit with parentheses
                transformed = transformed.replace('*Source:', '(Source code for the above example:')
                transformed = transformed.replace('*', ')')  # Replace trailing asterisk with closing parenthesis
                return transformed

            content = re.sub(r'\*Source: /unionai-examples[^\*]*\*', transform_source_ref, content)

            # Move source references directly after code blocks (remove blank line between them)
            content = re.sub(r'```\n\n\(Source code for the above example:', '```\n(Source code for the above example:', content)

            # Remove any other footer metadata section that might remain
            content = re.sub(r'\n---\n\*\*Source\*\*:.*?(?=\n\n|\Z)', '', content, flags=re.DOTALL)

            # This will be updated in process_page_depth_first to pass hierarchy
            # content = self.process_internal_links(content, file_path, hierarchy)

            # Clean up excessive whitespace but preserve structure
            content = content.rstrip() + '\n'

            return content
        except Exception as e:
            print(f"❌ Error reading {file_path}: {e}")
            return ""

    def process_internal_links(self, content: str, current_file_path: Path, current_hierarchy: List[str]) -> str:
        """Convert internal documentation links to hierarchical bold references."""
        def replace_internal_link(match):
            text = match.group(1)
            url = match.group(2)

            # Keep external links unchanged
            if url.startswith(('http://', 'https://', 'mailto:')):
                return match.group(0)

            # Convert same-page anchor links to hierarchical references
            if url.startswith('#'):
                anchor = url[1:]  # Remove the # prefix
                try:
                    rel_path = url_dir_of(self.variant_root, current_file_path).lower()
                except ValueError:
                    rel_path = current_file_path.name.lower()
                anchor_key = f"{rel_path}#{anchor}"
                if anchor_key in self.title_lookup:
                    hierarchical_title = self.title_lookup[anchor_key]
                    return f"**{hierarchical_title}**"
                # Fallback: use current page hierarchy + link text
                else:
                    current_page_title = self.strip_common_prefix(' > '.join(current_hierarchy))
                    return f"**{current_page_title} > {text}**"

            # For internal twin links (with or without anchors), convert to a
            # hierarchical reference. Stage 1 rewrote every resolvable page link
            # to `<path>.md`, so that suffix is the marker -- as `page.md` was
            # before the rename. Bundles are not pages and stay links.
            link_target = url.split('#', 1)[0]
            if (link_target.endswith(PAGE_SUFFIX)
                    and not url.startswith(('http://', 'https://'))):
                hierarchical_title = self.resolve_hierarchical_title(url, current_file_path, current_hierarchy, text)
                return f"**{hierarchical_title}**"

            # Keep other links unchanged (absolute paths like /docs/, static files, etc.)
            return match.group(0)

        # Protect code blocks and inline code spans from link processing
        code_spans = []
        def protect_code_span(match):
            code_spans.append(match.group(0))
            return f'\x00CODE{len(code_spans) - 1}\x00'
        # Fenced code blocks first (``` ... ```), then inline code spans (` ... `)
        content = re.sub(r'```[^`]*```', protect_code_span, content, flags=re.DOTALL)
        content = re.sub(r'`[^`]+`', protect_code_span, content)

        # Process markdown links
        content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace_internal_link, content)

        # Restore code spans
        for i, span in enumerate(code_spans):
            content = content.replace(f'\x00CODE{i}\x00', span)

        return content

    def resolve_hierarchical_title(self, url: str, current_file_path: Path, current_hierarchy: List[str], link_text: str) -> str:
        """Resolve hierarchical title using lookup table."""
        # Resolve the target file path
        target_path = self.resolve_link_path(url, current_file_path)

        # Look up in our title mapping
        if target_path in self.title_lookup:
            title = self.title_lookup[target_path]
            # Skip "Documentation > {VARIANT}" prefix
            return self.strip_common_prefix(title)

        # Fallback: use current hierarchy + link text (also strip prefix)
        # Track this as a resolution failure
        if current_hierarchy:
            full_title = f"{' > '.join(current_hierarchy)} > {link_text}"
            fallback_title = self.strip_common_prefix(full_title)
        else:
            fallback_title = link_text

        # Record the resolution failure
        self.resolution_issues.append({
            'source_file': self.current_source_file,
            'link_url': url,
            'link_text': link_text,
            'resolved_path': target_path,
            'fallback_title': fallback_title,
        })

        return fallback_title

    def strip_common_prefix(self, title: str) -> str:
        """Remove 'Documentation > {variant}' prefix from hierarchical titles."""
        parts = title.split(' > ')
        # Skip first two parts if they match the expected pattern
        if len(parts) >= 2 and parts[0] == 'Documentation':
            return ' > '.join(parts[2:]) if len(parts) > 2 else parts[-1]
        return title

    def resolve_link_path(self, url: str, current_file_path: Path) -> str:
        """Resolve a relative URL to the lookup key of the page it names.

        Keys are URL directories relative to the variant root (`''` for the
        root), which is what `build_lookup_tables()` stores.

        The base is `source_dir_of()`, the same base every other resolver in
        the generator uses. Before the rename `current_file_path.parent` was
        that base for every page, so this function agreed with the rest by
        coincidence; the rename breaks the coincidence for section landings,
        whose twin sits one level above their source directory. This keeps the
        function doing exactly what it did (link-issues.txt: 0 before, 0 after)
        rather than fixing the separate defect DOC-1499 tracks in it.
        """
        # Split URL and anchor
        if '#' in url:
            file_part, anchor = url.split('#', 1)
        else:
            file_part, anchor = url, None

        try:
            if file_part:
                resolved = self._resolve_from_source_dir(current_file_path, file_part)
            else:  # Just anchor, same file
                resolved = current_file_path

            # Get the URL directory relative to the variant root
            try:
                key = url_dir_of(self.variant_root, resolved).lower()
            except ValueError:
                # Fallback to filename only
                key = str(resolved.name).lower()

            if anchor:
                key = f"{key}#{anchor}"

            return key
        except Exception:
            return url.lower()

    def extract_page_title(self, content: str, file_path: Path) -> str:
        """Extract the main title from a markdown page."""
        # Look for the first # title
        title_match = re.search(r'^#\s+(.+?)\s*$', content, re.MULTILINE)
        if title_match:
            return title_match.group(1).strip()

        # Fallback to filename
        name = file_path.stem
        if name in ('index', 'content'):
            name = file_path.parent.name
        return name.replace('-', ' ').replace('_', ' ').title()

    def parse_heading_hierarchy(self, content: str, file_path: Path, page_hierarchy: List[str]) -> dict[str, str]:
        """Parse all headings and build anchor lookup table."""
        anchor_map = {}

        # Find all markdown headings
        heading_pattern = r'^(#{1,6})\s+(.+?)\s*$'
        headings = []

        for match in re.finditer(heading_pattern, content, re.MULTILINE):
            level = len(match.group(1))  # Number of # characters
            title = match.group(2).strip()
            anchor = self.title_to_anchor(title)
            headings.append((level, title, anchor))

        # Build hierarchical structure
        heading_stack = []  # Stack to track current hierarchy

        for level, title, anchor in headings:
            # Skip the main page title (# heading) since it's already in page_hierarchy
            if level == 1:
                heading_stack = [(level, title)]  # Reset stack with main title
                # Don't add to anchor_map for level 1 headings since they duplicate page title
                continue

            # Pop headings that are at same or deeper level
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()

            # Add current heading to stack
            heading_stack.append((level, title))

            # Build full hierarchical title - skip the first heading in stack (main title)
            heading_hierarchy = [h[1] for h in heading_stack[1:]]  # Skip first element
            full_hierarchy = page_hierarchy + heading_hierarchy
            hierarchical_title = ' > '.join(full_hierarchy)

            # Store in anchor map (strip common prefix)
            clean_title = self.strip_common_prefix(hierarchical_title)
            anchor_map[anchor] = clean_title

        return anchor_map

    def title_to_anchor(self, title: str) -> str:
        """Convert heading title to URL anchor format matching Hugo's behavior."""
        anchor = title.lower()
        # Remove special chars except alphanumeric, spaces, underscores, hyphens
        # Hugo removes chars like () but keeps spaces which become hyphens
        anchor = re.sub(r'[^a-zA-Z0-9\s_-]', '', anchor)
        # Replace whitespace with hyphens (each space becomes one hyphen)
        anchor = re.sub(r'\s', '-', anchor.strip())
        return anchor

    def extract_h2_h3_headings(self, content: str) -> List[str]:
        """Extract H2 and H3 heading titles from content for index/subpage listings."""
        headings = []
        for match in re.finditer(r'^(#{2,3})\s+(.+?)\s*$', content, re.MULTILINE):
            title = match.group(2).strip()
            if title.lower() != 'subpages':
                headings.append(title)
        return headings

    def format_subpage_entry(self, title: str, url: str, headings: List[str],
                             as_index: bool = False, description: str = "") -> str:
        """Format a page entry with H2/H3 headings.

        as_index=True:  Title|url + indented headings (for llms.txt pipe format)
        as_index=False: - [Title](url)[ - description] + indented headings

        The description is the child page's front-matter `description`, which
        Hugo has already written into the listing. It is carried through the
        rewrite, never re-derived. The pipe format has no column for it and its
        delimiter is a character a description may contain, so `as_index=True`
        ignores it (DOC-1508).
        """
        if as_index:
            lines = [f"{title}|{url}"]
        else:
            head = f"- [{title}]({url})"
            if description:
                head += f" - {description}"
            lines = [head]
        for heading in headings:
            lines.append(f"  - {heading}")
        return '\n'.join(lines)

    def extract_subpage_links(self, content: str) -> List[str]:
        """Extract links from ## Subpages section."""
        # Find the ## Subpages section
        subpages_pattern = r'## Subpages\s*\n(.*?)(?=\n##|\n---|\Z)'
        match = re.search(subpages_pattern, content, re.DOTALL | re.IGNORECASE)

        if not match:
            return []

        subpages_content = match.group(1).strip()

        # Extract markdown links
        links = []

        for link_match in SUBPAGE_ENTRY_RE.finditer(subpages_content):
            link_url = link_match.group(2)
            # Clean the URL (remove anchors, etc.)
            link_url = link_url.split('#')[0].strip()
            if link_url and not link_url.startswith(('http://', 'https://')):
                links.append(link_url)

        return links

    def build_consolidated_doc(self, variant: str, version: str = None) -> str:
        """Build consolidated document by following subpage links depth-first."""
        version = version or self.version
        variant_dir = (self.base_path / 'dist' / 'docs' / version / variant).resolve()

        if not variant_dir.exists():
            print(f"Error: Directory not found: {variant_dir}")
            return ""

        self.variant_root = variant_dir

        # Reset state for this variant
        self.resolution_issues.clear()
        self.index_entries.clear()
        self.page_headings.clear()
        self.section_pages.clear()
        self.current_source_file = ""

        if not self.quiet:
            print(f"Building consolidated document for {variant}")

        # First pass: Build lookup tables for all pages
        if not self.quiet:
            print("  First pass: Building lookup tables...")
        self.visited_files.clear()  # Reset for first pass
        self.build_lookup_tables(root_page(variant_dir), variant_dir, [])

        # Second pass: Process content with lookup tables populated
        if not self.quiet:
            print("  Second pass: Processing content...")
        consolidated_content = []
        self.process_page_depth_first(root_page(variant_dir), consolidated_content,
                                      variant_dir, [], variant, version)

        return '\n'.join(consolidated_content)

    def write_resolution_report(self, variant: str, version: str = None) -> Path:
        """Write a report of link resolution issues to a file."""
        version = version or self.version
        report_file = self.base_path / 'dist' / 'docs' / version / variant / 'link-issues.txt'

        with open(report_file, 'w', encoding='utf-8') as f:
            if self.resolution_issues:
                f.write(f"Found {len(self.resolution_issues)} link resolution issues:\n\n")
                for issue in self.resolution_issues:
                    f.write(f"{issue['source_file']}: Link [{issue['link_text']}]({issue['link_url']}) -> "
                           f"could not resolve, used fallback: \"{issue['fallback_title']}\"\n")
            else:
                f.write("No link resolution issues found.\n")

        return report_file

    def _page_for_link(self, current_file: Path, link: str) -> Optional[Path]:
        """The page file a `## Subpages` link names, or None if there is none."""
        resolved = self._resolve_from_source_dir(current_file, link)
        if resolved.suffix == PAGE_SUFFIX:
            return resolved if resolved.is_file() else None
        try:
            url_dir = url_dir_of(self.variant_root, resolved)
        except ValueError:
            return None
        page = page_for(self.variant_root, url_dir)
        return page if page.is_file() else None

    def build_lookup_tables(self, file_path: Path, md_root: Path, hierarchy: List[str] = None):
        """Build lookup tables for all pages without processing content."""
        if hierarchy is None:
            hierarchy = []

        # Avoid infinite loops
        canonical_path = str(file_path.resolve())
        if canonical_path in self.visited_files:
            return
        self.visited_files.add(canonical_path)

        if not file_path.exists():
            if not self.quiet:
                print(f"Warning: File not found: {file_path}")
            return

        # Lookup key: the URL directory this page serves, relative to the
        # variant root. Lowercased for case-insensitive matching (the macOS
        # filesystem is case-insensitive).
        try:
            relative_from_root = url_dir_of(md_root, file_path).lower()
        except ValueError:
            relative_from_root = str(file_path).lower()

        # Read the raw content
        raw_content = self.read_file_content(file_path)
        if not raw_content.strip():
            return

        # Extract page title and build hierarchy
        page_title = self.extract_page_title(raw_content, file_path)
        current_hierarchy = hierarchy + [page_title]
        hierarchical_title = ' > '.join(current_hierarchy)

        # Store page in lookup table (keys normalized to lowercase)
        self.title_lookup[relative_from_root] = hierarchical_title

        # Parse and store heading hierarchy for anchor links
        anchor_map = self.parse_heading_hierarchy(raw_content, file_path, current_hierarchy)
        for anchor, anchor_title in anchor_map.items():
            anchor_key = f"{relative_from_root}#{anchor}"
            self.title_lookup[anchor_key] = anchor_title

        # Extract H2/H3 headings for index/subpage listings
        h2h3 = self.extract_h2_h3_headings(raw_content)
        self.page_headings[relative_from_root] = h2h3

        # Extract subpages and recursively build lookup tables
        subpage_links = self.extract_subpage_links(raw_content)
        if subpage_links:
            self.section_pages.add(relative_from_root)
        for link in subpage_links:
            child = self._page_for_link(file_path, link)
            if child is None:
                if not self.quiet:
                    print(f"Warning: Could not find a page for: {link}")
                continue
            self.build_lookup_tables(child, md_root, current_hierarchy)

    def process_page_depth_first(self, file_path: Path,
                                consolidated: List[str], md_root: Path, hierarchy: List[str] = None,
                                variant: str = None, version: str = None):
        """Process a page and its subpages in depth-first order."""

        if hierarchy is None:
            hierarchy = []

        if not file_path.exists():
            if not self.quiet:
                print(f"Warning: File not found: {file_path}")
            return

        # The URL directory this page serves, relative to the variant root
        try:
            relative_from_root = url_dir_of(md_root, file_path)
        except ValueError:
            relative_from_root = str(file_path)

        if not self.quiet:
            print(f"  Processing: {relative_from_root}")

        # Track current source file for resolution issue reporting
        self.current_source_file = relative_from_root

        # Read the raw content
        raw_content = self.read_file_content(file_path)
        if not raw_content.strip():
            return

        # Extract page title and build hierarchy (for current processing)
        page_title = self.extract_page_title(raw_content, file_path)
        current_hierarchy = hierarchy + [page_title]

        # Extract subpages BEFORE processing links
        subpage_links = self.extract_subpage_links(raw_content)

        # Process internal links with lookup tables populated
        content = self.process_internal_links(raw_content, file_path, current_hierarchy)

        # Add page delimiter with URL
        if variant and version:
            web_path = relative_from_root

            url = f"https://www.union.ai/docs/{version}/{variant}/{web_path}".rstrip('/')
            consolidated.append(f"\n=== PAGE: {url} ===\n")

            # Collect index entry (with path_key for heading lookup). The
            # variant root has no twin, so it advertises its HTML URL; the
            # index skips it anyway (depth 0).
            stripped_title = self.strip_common_prefix(' > '.join(current_hierarchy))
            llm_url = f"{url}{PAGE_SUFFIX}" if web_path else url
            self.index_entries.append((stripped_title, llm_url, relative_from_root.lower()))
        else:
            consolidated.append(f"\n=== PAGE: {relative_from_root} ===\n")
        consolidated.append(content)

        # Process subpages depth-first
        for link in subpage_links:
            if not self.quiet:
                print(f"    Following: {link}")
            child = self._page_for_link(file_path, link)
            if child is None:
                continue
            self.process_page_depth_first(child, consolidated, md_root,
                                          current_hierarchy, variant, version)

    def find_variants(self) -> List[str]:
        """Find available variants in the dist directory."""
        dist_path = self.base_path / "dist" / "docs" / self.version
        if not dist_path.exists():
            return []

        variants = []
        for item in dist_path.iterdir():
            if item.is_dir() and root_page(item).exists():
                variants.append(item.name)

        return sorted(variants)

    def _path_depth(self, path_key: str) -> int:
        """Get the directory depth of a path_key (0 = the variant root)."""
        parts = [p for p in path_key.strip('/').split('/') if p]
        return len(parts)

    def _frontmatter_title(self, path_key: str) -> str:
        """Extract frontmatter title from the source _index.md file."""
        dir_path = path_key.strip('/')
        if dir_path:
            source_file = self.content_root / dir_path / '_index.md'
        else:
            source_file = self.content_root / '_index.md'

        if not source_file.exists():
            return ''

        try:
            with open(source_file, 'r', encoding='utf-8') as f:
                content = f.read()
            match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if match:
                for line in match.group(1).split('\n'):
                    if line.startswith('title:'):
                        return line.split(':', 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return ''

    def create_index_content(self, variant: str) -> str:
        """Create a page index for llms.txt.

        Lists all pages grouped by top-level section, each with
        H2/H3 headings for discoverability.
        """
        variant_names = {
            'flyte': 'Flyte Open Source',
            'union': 'Union.ai'
        }

        variant_display = variant_names.get(variant, variant.title())
        base_url = f"https://www.union.ai/docs/{self.version}/{variant}"

        lines = [
            f"# {variant_display} Documentation",
        ]
        if self.version != "v2":
            lines.extend([
                f"> **This is legacy ({self.version}) documentation.** Do not use"
                " unless explicitly asked about this version."
                f" For current documentation, see https://www.union.ai/docs/v2/llms.txt",
                "",
            ])
        lines.extend([
            f"> Full documentation (single file): {base_url}/llms-full.txt",
            f"> Site: {base_url}",
            "",
            "Each entry below is `- [Page title](URL)` followed by the"
            " H2/H3 headings found on that page."
            " Each page's markdown is served at its own URL with `.md`"
            " appended, e.g. `/integrations/hydra.md`."
            " A section landing page's markdown also lists every page directly"
            " beneath it, each with a one-line description, so it is the index"
            " for that section.",
            "",
        ])

        # Group entries by top-level section
        sections = []
        current_section = None

        for title, url, path_key in self.index_entries:
            depth = self._path_depth(path_key)

            if depth == 0:
                # Root page — skip
                continue
            elif depth == 1:
                # Top-level entry (section or standalone page)
                if current_section is not None:
                    sections.append(current_section)
                current_section = {
                    'title': title,
                    'display_name': self._frontmatter_title(path_key) or title,
                    'url': url,
                    'path_key': path_key,
                    'children': []
                }
            else:
                # Deeper page — belongs to current section
                if current_section is not None:
                    current_section['children'].append((title, url, path_key))

        if current_section is not None:
            sections.append(current_section)

        # Format each section
        for i, section in enumerate(sections):
            if i > 0:
                lines.append("---")
                lines.append("")

            lines.append(f"## {section['display_name']}")
            lines.append("")

            if section['children']:
                for child_title, child_url, child_key in section['children']:
                    # Strip section prefix from title
                    prefix = section['title'] + ' > '
                    relative_title = child_title[len(prefix):] if child_title.startswith(prefix) else child_title

                    headings = self.page_headings.get(child_key, [])
                    entry = self.format_subpage_entry(
                        relative_title, child_url, headings)
                    lines.append(entry)


                lines.append("")

            else:
                # Standalone page at top level
                headings = self.page_headings.get(section['path_key'], [])
                entry = self.format_subpage_entry(
                    section['display_name'], section['url'], headings)
                lines.append(entry)
                lines.append("")

        return '\n'.join(lines)

    def enhance_subpage_listings(self, variant: str, version: str = None):
        """Post-process page twins to enhance ## Subpages sections with H2/H3 headings."""
        version = version or self.version
        variant_dir = (self.base_path / 'dist' / 'docs' / version / variant).resolve()
        self.variant_root = variant_dir
        base_url = f"https://www.union.ai/docs/{version}/{variant}"

        for content_file in iter_pages(variant_dir):
            try:
                relative_key = url_dir_of(variant_dir, content_file).lower()
            except ValueError:
                continue

            if relative_key not in self.section_pages:
                continue

            # This is a section page — enhance its subpage listing
            content = content_file.read_text(encoding='utf-8')

            # Parse existing subpage links
            subpages_pattern = r'## Subpages\s*\n(.*?)(?=\n##|\n---|\Z)'
            match = re.search(subpages_pattern, content, re.DOTALL | re.IGNORECASE)
            if not match:
                continue

            subpages_content = match.group(1).strip()

            enhanced_lines = ["## Subpages\n"]

            for link_match in SUBPAGE_ENTRY_RE.finditer(subpages_content):
                child_title = link_match.group(1)
                child_url = link_match.group(2)
                child_desc = subpage_description(link_match.group('tail'))
                child_path_part = child_url.split('#')[0].strip()

                if not child_path_part or child_path_part.startswith(('http://', 'https://')):
                    # An external child has no twin to read headings from, but
                    # its description came from the same template and stays.
                    enhanced_lines.append(self.format_subpage_entry(
                        child_title, child_url, [], description=child_desc))
                    continue

                # Resolve the child to get the path key for heading lookup
                child_path = self._resolve_from_source_dir(content_file, child_path_part)
                try:
                    child_key = url_dir_of(variant_dir, child_path).lower()
                except ValueError:
                    child_key = ""

                # A heading may itself be a link, and its URL was written
                # against the CHILD page. Copying it into the parent's listing
                # moves it to a page with a different base, so resolve it here
                # while the child is still known. Left relative it would be
                # absolutized later against the parent and point at a page that
                # does not exist -- or worse, at one that does (DOC-1499).
                headings = [
                    self.absolutize_in(h, page_for(variant_dir, child_key), base_url)[0]
                    for h in self.page_headings.get(child_key, [])
                ]
                entry = self.format_subpage_entry(
                    child_title, child_url, headings, description=child_desc)
                enhanced_lines.append(entry)

            enhanced_table = '\n'.join(enhanced_lines)

            # Replace the existing ## Subpages section
            new_content = re.sub(subpages_pattern, enhanced_table + '\n', content,
                                 flags=re.DOTALL | re.IGNORECASE)

            content_file.write_text(new_content, encoding='utf-8')

        if not self.quiet:
            print(f"Enhanced subpage listings for {variant}")

    def _resolve_from_source_dir(self, current_file: Path, link_path: str) -> Path:
        """Resolve a relative markdown link written in a Hugo source file against
        the output tree, given the output file that carries it.

        The base is the SOURCE directory the link was authored against, which
        `page_paths.source_dir_of()` derives from the Hugo source tree:

          * a leaf page `content/a/b/foo.md` is written out as `a/b/foo.md`, so
            its source directory is the twin's own parent, `a/b`;
          * a section landing `content/a/b/_index.md` is written out as
            `a/b.md`, so its source directory is `a/b` -- one level DEEPER than
            the file sits.

        Before the rename the offset ran the other way and this resolved
        literally, falling back one level UP when the target did not exist. That
        guard cannot survive the rename: it goes the wrong direction for every
        section landing, and being an existence check it would not error -- it
        would silently point links at the wrong page. So the shape of the output
        path is no longer consulted at all; the source tree decides.

        Used by every resolver here and by stage 1; keep it that way rather than
        reintroducing a per-call-site copy (the bundle path carried the only
        copy for a while, which is exactly how `page.md` shipped every
        cross-directory link broken -- DOC-1494).
        """
        # A link may name a section's source file explicitly
        # (`../task-deployment/_index`). Hugo resolves that to the section
        # itself; carried through literally it becomes a 404 URL.
        link_path = re.sub(r'(^|/)_index(\.md)?/?$', r'\1', link_path)
        link_path = link_path.rstrip('/') or '.'

        base = source_dir_of(self.variant_root, current_file, self.content_root)
        return (base / link_path).resolve()

    def _published_path(self, resolved: Path) -> Optional[str]:
        """The variant-relative path an internal link should publish, from a
        resolved filesystem target. `None` when the target is outside the
        variant tree, i.e. not ours to rewrite.

        **A link to a page publishes that page's `.md` twin.** After the
        DOC-1432 rename every page exists in the built tree as BOTH a twin file
        and a directory -- `ray.md` AND `ray/` -- so a resolved path lands on
        whichever of the two the author happened to write:

            [Ray](./ray)          -> the DIRECTORY  ray/     -> `.../ray`
            [Ray](./ray/_index)   -> the DIRECTORY  ray/     -> `.../ray`
            [Ray](./ray.md)       -> the twin FILE  ray.md   -> `.../ray.md`

        All three mean the same page, and all three are legitimate authoring.
        Publishing the resolved path made a quarter of the corpus's internal
        links point at the nav-first HTML page instead of the markdown an agent
        came for (DOC-1507). So the twin is derived from the path rather than
        read off where resolution landed, and the `_index` form is not special-
        cased -- any resolution that lands on a page directory yields the twin.

        Three targets deliberately keep the path they resolved to:

          * a **`_section.md` bundle**, which is not a page twin;
          * the **variant root**, which has no twin at all (DOC-1432 A2) -- its
            twin would be `<variant>.md`, a sibling of the whole variant tree,
            where the site serves an HTML redirect;
          * a target with **no twin on disk** (a broken link, an image, an
            asset), which stays a visible 404 rather than being relocated onto
            some other page (the DOC-1499 wrong-200).
        """
        try:
            rel = str(resolved.relative_to(self.variant_root))
        except ValueError:
            return None
        rel = rel.replace('\\', '/').strip('/')
        if rel in ('', '.'):
            # The variant root. No twin exists; emit what resolution produced.
            return rel
        if resolved.suffix == PAGE_SUFFIX:
            # Already a twin, or a bundle. Either way, publish it as it is.
            return rel
        twin = page_for(self.variant_root, rel)
        return rel + PAGE_SUFFIX if twin.is_file() else rel

    def absolutize_in(self, text: str, base_file: Path, base_url: str) -> Tuple[str, int]:
        """Rewrite every relative markdown link in `text` to an absolute URL.

        `base_file` is the page the link was authored in, which is NOT always the
        page the text ends up in. A heading copied out of a child page into its
        parent's `## Subpages` listing carries a link written against the CHILD,
        so it has to be resolved here, at copy time, against that child -- not
        later against the parent that now holds it (DOC-1499, DOC-1511).

        Returns the rewritten text and the number of links changed. The count is
        the assertion: a rewrite pass that silently matches nothing reports
        success, so callers check the number rather than the exit status.
        """
        count = 0

        def replace_link(match):
            nonlocal count
            link_text, link_url = match.groups()

            # A markdown link may wrap across lines, which puts a newline and
            # the following indent INSIDE the captured target. Left in place it
            # defeats the scheme test below, so an external `https://github.com/...`
            # is taken for a relative path and gets the docs base glued in front
            # of it. Strip first, then classify.
            link_url = link_url.strip()

            # Skip external links
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', link_url):
                return match.group(0)

            # Skip anchor-only links
            if link_url.startswith('#'):
                return match.group(0)

            # Handle root-relative paths (e.g. /docs/v2/flyte/...)
            if link_url.startswith('/'):
                count += 1
                return f'[{link_text}](https://www.union.ai{link_url})'

            # Split URL and anchor
            url_parts = link_url.split('#', 1)
            base_path_part = url_parts[0]
            anchor = '#' + url_parts[1] if len(url_parts) > 1 else ''

            if not base_path_part:
                return match.group(0)

            # Resolve relative path to absolute filesystem path
            resolved = self._resolve_from_source_dir(base_file, base_path_part)

            # The page's twin, not whichever of the twin/directory pair the
            # link happened to resolve to. The anchor rides on the END of
            # the URL, after the `.md`.
            rel_to_variant = self._published_path(resolved)
            if rel_to_variant is None:
                return match.group(0)

            absolute_url = f"{base_url}/{rel_to_variant}{anchor}"
            count += 1
            return f'[{link_text}]({absolute_url})'

        return re.sub(MD_LINK_RE, replace_link, text), count

    def absolutize_links(self, variant: str, version: str = None):
        """Convert all relative links in the page twins to absolute URLs."""
        version = version or self.version
        variant_dir = (self.base_path / 'dist' / 'docs' / version / variant).resolve()
        self.variant_root = variant_dir
        base_url = f"https://www.union.ai/docs/{version}/{variant}"
        fixed_count = 0
        total_files = 0

        for content_file in iter_page_twins(variant_dir):
            total_files += 1
            try:
                content = content_file.read_text(encoding='utf-8')
            except Exception:
                continue

            new_content, changed = self.absolutize_in(content, content_file, base_url)
            fixed_count += changed

            if new_content != content:
                content_file.write_text(new_content, encoding='utf-8')

        if not self.quiet:
            print(f"Converted {fixed_count} links to absolute URLs in {total_files} files for {variant}")

    def _strip_subpages_section(self, content: str) -> str:
        """Remove ## Subpages section from content."""
        return re.sub(r'\n## Subpages\s*\n.*?(?=\n---\n|\Z)', '', content, flags=re.DOTALL)

    def _immediate_children(self, content_file: Path) -> List[Path]:
        """The page twins one level below a section landing page.

        Read off the landing page's own `## Subpages` table, so the order is
        Hugo's weight order rather than the filesystem's. Paths are resolved with
        `_resolve_from_source_dir()` -- the same helper `absolutize_links()` uses
        -- so the bundle and the per-page output can never disagree about where a
        link points (DOC-1494).
        """
        content = content_file.read_text(encoding='utf-8')
        children: List[Path] = []
        for link in self.extract_subpage_links(content):
            child_page = self._page_for_link(content_file, link)
            if child_page is not None and child_page not in children:
                children.append(child_page)
        return children

    def _has_subpages(self, content_file: Path) -> bool:
        """True when this page has anything beneath it, i.e. it is a section
        with children and therefore carries a `_section.md` of its own."""
        try:
            return bool(self.extract_subpage_links(
                content_file.read_text(encoding='utf-8')))
        except OSError:
            return False

    def _is_source_section(self, dir_path: str) -> bool:
        """True when this URL directory came from a Hugo section (`_index.md`)
        rather than from a leaf page.

        The output tree cannot tell the two apart -- Hugo gives every page a
        pretty URL, so a leaf twin sits beside a same-named directory exactly as
        a section landing does -- and the manifest header has to, because it
        counts sub-sections. So ask the source tree. Same test the link
        resolver uses; one implementation, in page_paths.py.
        """
        return is_section_landing(self.content_root, dir_path)

    def _frontmatter_description(self, dir_path: str) -> str:
        """The `description` from a section's source `_index.md`, if it has one."""
        if dir_path:
            source_file = self.content_root / dir_path / '_index.md'
        else:
            source_file = self.content_root / '_index.md'
        if not source_file.exists():
            return ''
        try:
            content = source_file.read_text(encoding='utf-8')
            match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if match:
                for line in match.group(1).split('\n'):
                    if line.startswith('description:'):
                        return line.split(':', 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
        return ''

    @staticmethod
    def _first_paragraph(text: str) -> str:
        """The page's first real paragraph, skipping the H1, notes and code."""
        buf: List[str] = []
        in_fence = False
        for line in text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('```'):
                in_fence = not in_fence
                if buf:
                    break
                continue
            if in_fence:
                continue
            if not stripped:
                if buf:
                    break
                continue
            if stripped.startswith(('#', '>', '|', '<', '===')):
                if buf:
                    break
                continue
            buf.append(stripped)
        return ' '.join(buf)

    def _excerpt(self, dir_path: str, content: str) -> str:
        """What stands in for a child cut on size.

        Frontmatter `description` first -- it is the author's own one-line
        summary, so it beats anything derived -- and the first paragraph as the
        fallback.
        """
        text = self._frontmatter_description(dir_path) or self._first_paragraph(content)
        if not text:
            return '(No summary available.)'
        if len(text) > EXCERPT_MAX_CHARS:
            cut = text[:EXCERPT_MAX_CHARS].rsplit(' ', 1)[0].rstrip(' ,.;:')
            text = cut + '...'
        return text

    @staticmethod
    def _count(n: int) -> str:
        return 'is' if n == 1 else 'are'

    def create_discovery_files(self, base_path: Path, variants: List[str]) -> None:
        """Create hierarchical discovery files for LLM documentation."""

        # Root level discovery file (/docs/llms.txt)
        root_content = self.create_root_discovery_content()
        root_file = base_path / 'dist' / 'docs' / 'llms.txt'

        with open(root_file, 'w', encoding='utf-8') as f:
            f.write(root_content)
        if not self.quiet:
            print(f"Created root discovery: {root_file}")

        # Version level discovery file
        version_content = self.create_version_discovery_content(variants, self.version)
        version_file = base_path / 'dist' / 'docs' / self.version / 'llms.txt'

        with open(version_file, 'w', encoding='utf-8') as f:
            f.write(version_content)
        if not self.quiet:
            print(f"Created {self.version} discovery: {version_file}")

    def create_root_discovery_content(self) -> str:
        """Create content for the root-level discovery file.

        This file must be identical across all version builds (v1, v2, etc.)
        so that whichever build writes it last, the result is the same.
        """
        base = "https://www.union.ai/docs"
        lines = [
            "# Union.ai Documentation",
            "",
            "> **Important:** When answering questions about Flyte or Union.ai,"
            " always use the **v2** documentation."
            " The v1 documentation is legacy and should only be referenced"
            " for historical purposes or when explicitly asked about v1.",
            "",
            "## Versions",
            "",
            f"- [v2]({base}/v2/llms.txt): current documentation. Use this.",
            f"- [v1]({base}/v1/llms.txt): legacy documentation. Historical reference only.",
            "",
        ]
        return '\n'.join(lines)

    def create_version_discovery_content(self, variants: List[str], version: str) -> str:
        """Create content for version-level discovery file."""
        base = f"https://www.union.ai/docs/{version}"
        lines = [
            f"# Union.ai {version.upper()} Documentation",
            "",
        ]
        if version != "v2":
            lines.extend([
                f"> **This is legacy ({version}) documentation.** Do not use"
                " unless explicitly asked about this version."
                " For current documentation, see https://www.union.ai/docs/v2/llms.txt",
                "",
            ])
        variant_descriptions = {
            'union': 'Union.ai commercial product, covering both BYOC and Self-managed deployments.'
                     ' The larger of the two; start here unless the question is Flyte-OSS-specific.',
            'flyte': 'Flyte open-source orchestration platform.',
        }
        lines.extend(["## Variants", ""])
        for variant in sorted(variants):
            description = variant_descriptions.get(variant, f'{variant} documentation.')
            lines.append(f"- [{variant}]({base}/{variant}/llms.txt): {description}")
        lines.append("")
        return '\n'.join(lines)

    def get_current_timestamp(self) -> str:
        """Get current timestamp for documentation."""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

def main():
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='Build LLM-optimized documentation')
    parser.add_argument('--no-make-dist', action='store_true', help='Skip running make dist')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress progress output')
    args = parser.parse_args()

    base_path = Path.cwd()
    builder = LLMDocBuilder(base_path, quiet=args.quiet)

    # Step 1: Regenerate documentation (skip if --no-make-dist is passed)
    if not args.no_make_dist and not builder.run_make_dist():
        return 1

    # Step 2: Find variants
    variants = builder.find_variants()
    if not variants:
        print("Error: No variants found")
        return 1

    if not args.quiet:
        print(f"Found variants: {variants}")

    # Step 3: Build consolidated documents
    for variant in variants:
        consolidated_content = builder.build_consolidated_doc(variant)

        if consolidated_content.strip():
            # Create output file
            output_file = base_path / 'dist' / 'docs' / builder.version / variant / 'llms-full.txt'

            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(consolidated_content)

            if not args.quiet:
                file_size = len(consolidated_content)
                print(f"Saved: {output_file} ({file_size:,} characters)")

            # Enhance the twins' subpage listings with H2/H3 headings
            builder.enhance_subpage_listings(variant)


            # Convert relative links to absolute URLs
            builder.absolutize_links(variant)

            # Create llms.txt page index
            redirect_file = base_path / 'dist' / 'docs' / builder.version / variant / 'llms.txt'
            redirect_content = builder.create_index_content(variant)

            with open(redirect_file, 'w', encoding='utf-8') as f:
                f.write(redirect_content)

            if not args.quiet:
                print(f"Created redirect: {redirect_file}")

            # Write resolution issues report
            report_file = builder.write_resolution_report(variant)
            issue_count = len(builder.resolution_issues)
            if issue_count > 0:
                print(f"Found {issue_count} link resolution issues for {variant}:")
                for issue in builder.resolution_issues[:10]:  # Show first 10
                    print(f"  {issue['source_file']}: [{issue['link_text']}]({issue['link_url']})")
                if issue_count > 10:
                    print(f"  ... and {issue_count - 10} more issues")
                print(f"  Full list: {report_file}")
            elif not args.quiet:
                print(f"No link resolution issues for {variant}")
        else:
            print(f"Error: No content generated for {variant}")

        # The variant root's rendering was stage 1's hand-off, not a served
        # artifact (DOC-1432 A2). Everything downstream of it has been written,
        # so remove it before the tree is deployed.
        root_file = root_page(base_path / 'dist' / 'docs' / builder.version / variant)
        if root_file.exists():
            root_file.unlink()

    # Step 4: Create hierarchical discovery files
    builder.create_discovery_files(base_path, variants)

    return 0

if __name__ == '__main__':
    exit(main())