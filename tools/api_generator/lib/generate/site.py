import os
from typing import Dict, List

from lib.generate.classes import generate_class_index, generate_classes
from lib.generate.docstring import docstring_summary
from lib.generate.hugo import FrontMatterExtra, set_variants, set_version, write_front_matter
from lib.generate.packages import (
    generate_package_folders,
    generate_package_index,
)
from lib.generate.linkmap import generate_linkmap_metadata
from lib.ptypes import ParsedInfo

PackageTree = Dict[str, List[str]]


def generate_home_directory(source: ParsedInfo, output, ignore_types: List[str]):
    """Emit a "Directory" on the landing page listing the section's packages and
    classes, linking to the generated `packages/` and `classes/` child index pages.

    Without this, a landing page (the section's top-level `_index.md`) shows only the
    title plus whatever prose the `--include` template carries. Plugin integrations use
    a shared, empty include (`include/api.plugin.md`), so every plugin landing page
    rendered as an empty body. This gives each landing page a real index of its
    contents — the same Packages/Classes summary the child index pages already show.
    """
    packages = source.get("packages", [])
    classes = source.get("classes", {})

    # Packages that have any documented content (classes, methods, or variables).
    listed_packages = [
        pkg
        for pkg in packages
        if (
            len(classes.get(pkg["name"], {})) > 0
            or len(pkg.get("methods", [])) > 0
            or len(pkg.get("variables", [])) > 0
        )
    ]

    # All non-ignored classes across packages, with their owning package.
    class_rows = []
    for pkg_name, pkg_classes in classes.items():
        for cls in pkg_classes:
            if cls in ignore_types:
                continue
            class_rows.append((cls, pkg_classes[cls]))

    if not listed_packages and not class_rows:
        return

    output.write("## Directory\n\n")

    if class_rows:
        output.write("### Classes\n\n")
        output.write("| Class | Description |\n")
        output.write("|-|-|\n")
        for cls, cls_info in sorted(class_rows, key=lambda r: r[0]):
            output.write(
                f"| [`{cls}`](classes) | {docstring_summary(cls_info.get('doc', ''))} |\n"
            )
        output.write("\n")

    if listed_packages:
        output.write("### Packages\n\n")
        output.write("| Package | Description |\n")
        output.write("|-|-|\n")
        for pkg in listed_packages:
            output.write(
                f"| [`{pkg['name']}`](packages/{pkg['name']}) | {docstring_summary(pkg.get('doc', ''))} |\n"
            )
        output.write("\n")


def generate_home(
    title: str,
    source: ParsedInfo,
    include: List[str],
    doc_level: int,
    pkg_root: str,
    output_folder: str,
    weight: int,
    expanded: bool,
    ignore_types: List[str],
):
    with open(os.path.join(output_folder, "_index.md"), "w") as output:
        write_front_matter(title, output, {
            "expand_sidebar": expanded,
            "weight": weight,
        })
        output.write(f"# {title}\n\n")

        for inc in include:
            with open(inc, "r") as f:
                output.write(f.read())
                output.write("\n\n")

        generate_home_directory(source, output, ignore_types)

def generate_site(
    title: str,
    source: ParsedInfo,
    include: List[str],
    doc_level: int,
    output_folder: str,
    variants: List[str],
    flatten: bool,
    ignore_types: List[str],
    weight: int,
    expanded: bool,
    api_name: str | None,
    include_short_names: bool = False,
):
    set_variants(variants)
    set_version(source["version"])

    os.makedirs(output_folder, exist_ok=True)

    pkg_root = os.path.join(output_folder, "packages")
    os.makedirs(pkg_root, exist_ok=True)

    # Generate site
    generate_home(
        title=title,
        source=source,
        include=include,
        doc_level=doc_level,
        output_folder=output_folder,
        pkg_root=pkg_root,
        weight=weight,
        expanded=expanded,
        ignore_types=ignore_types,
    )

    subpages_frontmatter_extra: FrontMatterExtra = {
        "expand_sidebar": expanded,
        "weight": None,
    }

    generate_package_index(
        packages=source["packages"],
        classes=source["classes"],
        pkg_root=pkg_root,
        frontmatter_extra=subpages_frontmatter_extra,
    )

    generate_class_index(
        classes=source["classes"],
        output_folder=output_folder,
        pkg_root=pkg_root,
        flatten=flatten,
        ignore_types=ignore_types,
        frontmatter_extra=subpages_frontmatter_extra,
    )

    generate_package_folders(
        packages=source["packages"],
        classes=source["classes"],
        pkg_root=pkg_root,
        flatten=flatten,
        ignore_types=ignore_types,
        frontmatter_extra=subpages_frontmatter_extra,
    )

    if flatten:
        pass
    else:
        generate_classes(classes=source["classes"], pkg_root=pkg_root, ignore_types=ignore_types)

    if api_name:
        generate_linkmap_metadata(
            packages=source["packages"],
            classes=source["classes"],
            pkg_root=pkg_root,
            api_name=api_name,
            include_short_names=include_short_names,
            flatten=flatten,
        )