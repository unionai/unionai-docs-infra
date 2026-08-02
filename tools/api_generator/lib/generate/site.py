import os
from typing import Dict, List

from lib.generate.classes import ProtocolBaseClass, generate_classes
from lib.generate.docstring import docstring_summary
from lib.generate.helper import generate_anchor_from_name
from lib.generate.hugo import FrontMatterExtra, set_variants, set_version, write_front_matter
from lib.generate.packages import generate_package_folders
from lib.generate.linkmap import generate_linkmap_metadata
from lib.ptypes import ParsedInfo

PackageTree = Dict[str, List[str]]


def generate_home_directory(source: ParsedInfo, output, ignore_types: List[str],
                            single_package_flat: bool = False):
    """Emit the landing page's directory as KIND-BASED tables (DOC-1335):
    Classes / Protocols / Functions / Packages.

    This is the section's one index. The former `classes/` and `packages/` child
    index pages were strict re-listings of it and are no longer generated; the
    Protocols split (previously only on `classes/`) and the Functions table
    (previously nowhere -- module-level callables were findable only inside their
    module page) live here now. An unmarked protocol in a "Class" table implies
    instantiability, which is why the kinds are separated (the same distinction
    Javadoc/Rustdoc/TypeDoc draw).

    Links are relative to the landing page. With the `packages/` wrapper hoisted
    (DOC-1335) a class lives at `<pkg>/<leaf>`; when the whole section holds a
    single package (every plugin integration), `single_package_flat` collapses the
    module level too and a class lives at `<leaf>`.
    """
    if single_package_flat:
        # The module body (its Directory, Methods, Variables) is merged into this
        # same landing page by generate_package_folders -- a second directory here
        # would duplicate every table.
        return

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

    # All non-ignored classes across packages, split by kind.
    class_rows = []
    protocol_rows = []
    for pkg_name, pkg_classes in classes.items():
        for cls in pkg_classes:
            if cls in ignore_types:
                continue
            row = (cls, pkg_name, pkg_classes[cls])
            if pkg_classes[cls].get("parent") == ProtocolBaseClass:
                protocol_rows.append(row)
            else:
                class_rows.append(row)

    # Module-level functions, fully qualified, linking to their anchor on the
    # module page (functions are documented inline there, never as own pages).
    function_rows = []
    for pkg in listed_packages:
        for m in pkg.get("methods", []):
            function_rows.append((pkg["name"], m))

    if not listed_packages and not class_rows and not protocol_rows:
        return

    def class_target(cls: str, pkg_name: str) -> str:
        leaf = cls.split(".")[-1].lower()
        if single_package_flat:
            return leaf
        return f"{pkg_name}/{leaf}"

    def module_target(pkg_name: str) -> str:
        # On the landing page itself when the section is a single flat package.
        return "" if single_package_flat else f"{pkg_name}/_index"

    output.write("## Directory\n\n")

    def kind_table(title: str, header: str, rows):
        if not rows:
            return
        output.write(f"### {title}\n\n")
        output.write(f"| {header} | Description |\n")
        output.write("|-|-|\n")
        for cls, pkg_name, cls_info in rows:
            output.write(
                f"| [`{cls}`]({class_target(cls, pkg_name)}) | {docstring_summary(cls_info.get('doc', ''))} |\n"
            )
        output.write("\n")

    kind_table("Classes", "Class", class_rows)
    kind_table("Protocols", "Protocol", protocol_rows)

    if function_rows and not single_package_flat:
        # Single-flat sections already carry the full Methods section inline on
        # this same page (the module body is merged in), so a table of links to
        # it would be self-referential noise.
        output.write("### Functions\n\n")
        output.write("| Function | Description |\n")
        output.write("|-|-|\n")
        for pkg_name, m in function_rows:
            anchor = generate_anchor_from_name(m["name"])
            output.write(
                f"| [`{pkg_name}.{m['name']}()`]({pkg_name}/_index#{anchor}) | {docstring_summary(m.get('doc', ''))} |\n"
            )
        output.write("\n")

    if listed_packages and not single_package_flat:
        output.write("### Packages\n\n")
        output.write("| Package | Description |\n")
        output.write("|-|-|\n")
        for pkg in listed_packages:
            # Explicit `/_index` -- <name> is a section dir; the link-checker
            # resolves the section form unambiguously.
            output.write(
                f"| [`{pkg['name']}`]({module_target(pkg['name'])}) | {docstring_summary(pkg.get('doc', ''))} |\n"
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
    single_package_flat: bool = False,
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

        generate_home_directory(source, output, ignore_types,
                                single_package_flat=single_package_flat)

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

    # DOC-1335 depth reduction: the former `packages/` wrapper is HOISTED -- module
    # dirs sit directly under the section root (`flyte-sdk/<module>/<class>`). Its
    # index page, and the `classes/` index, are no longer generated: both were
    # strict re-listings of the landing page's directory (verified entry-by-entry
    # on the live site, 2026-08-02); the landing's kind tables carry everything.
    pkg_root = output_folder

    # A section whose content is a SINGLE package (every plugin integration --
    # `integrations/ray/` holding exactly `flyteplugins.ray`) also collapses the
    # module level: class pages sit at the section root and the module body is
    # merged into the landing page. Two path segments that restated the parent
    # (`ray/packages/flyteplugins.ray/`) become zero. Auto-detected, so a plugin
    # that ever grows a second package regains the module level without flags.
    contentful = [
        pkg for pkg in source["packages"]
        if (
            len(source["classes"].get(pkg["name"], {})) > 0
            or len(pkg.get("methods", [])) > 0
            or len(pkg.get("variables", [])) > 0
        )
    ]
    single_package_flat = len(contentful) == 1 and not flatten

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
        single_package_flat=single_package_flat,
    )

    subpages_frontmatter_extra: FrontMatterExtra = {
        "expand_sidebar": expanded,
        "weight": None,
    }

    generate_package_folders(
        packages=source["packages"],
        classes=source["classes"],
        pkg_root=pkg_root,
        flatten=flatten,
        ignore_types=ignore_types,
        frontmatter_extra=subpages_frontmatter_extra,
        single_package_flat=single_package_flat,
    )

    if flatten:
        pass
    else:
        generate_classes(classes=source["classes"], pkg_root=pkg_root, ignore_types=ignore_types,
                         single_package_flat=single_package_flat)

    if api_name:
        generate_linkmap_metadata(
            packages=source["packages"],
            classes=source["classes"],
            pkg_root=pkg_root,
            api_name=api_name,
            include_short_names=include_short_names,
            flatten=flatten,
            single_package_flat=single_package_flat,
        )