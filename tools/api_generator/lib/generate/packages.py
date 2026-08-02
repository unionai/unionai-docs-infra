import inspect
import os
import re
from typing import Dict, List, Optional

from lib.generate.classes import generate_class_details, generate_classes_and_error_list
from lib.generate.docstring import docstring_summary
from lib.generate.hugo import write_front_matter, FrontMatterExtra
from lib.ptypes import ClassPackageMap, PackageInfo
from lib.generate.methods import generate_method, generate_method_list
from lib.generate.properties import generate_props

PackageTree = Dict[str, List[str]]


def convert_package_list_to_tree(pkgs: List[PackageInfo]) -> PackageTree:
    result = {}

    for pkg in pkgs:
        parts = pkg["name"].split(".")
        glued = ""
        for p in parts:
            parent = glued
            glued = f"{glued}.{p}" if glued != "" else p
            if glued not in result:
                result[glued] = []
            if parent != "" and glued not in result[parent]:
                result[parent].append(glued)

    return result


def generate_package_index(
    pkg_root: str, packages: List[PackageInfo], classes: ClassPackageMap, frontmatter_extra: FrontMatterExtra
):
    # Check if any package has classes defined
    has_classes = any(bool(pkg_classes) for pkg_classes in classes.values())
    if not has_classes:
        return

    pkg_index = os.path.join(pkg_root, "_index.md")
    with open(pkg_index, "w") as index:
        write_front_matter("Packages", index, frontmatter_extra)

        index.write("# Packages\n\n")

        index.write("| Package | Description |\n")
        index.write("|-|-|\n")

        for pkg in packages:
            if len(classes[pkg["name"]]) == 0 and len(pkg["methods"]) == 0 and len(pkg["variables"]) == 0:
                # Skip packages with no classes, methods, or variables
                continue
            doc = pkg["doc"] if "doc" in pkg else ""
            # Explicit `/_index`: each package is a section dir; the link-checker
            # requires the explicit index form for section links. (The bare form is
            # grandfathered in already-generated pages; new/regenerated content uses
            # this form.)
            index.write(
                f"| [`{pkg['name']}`]({pkg['name']}/_index) | {docstring_summary(doc)} |\n"
            )


def generate_package_folders(
    packages: List[PackageInfo],
    classes: ClassPackageMap,
    pkg_root: str,
    flatten: bool,
    ignore_types: List[str],
    frontmatter_extra: Optional[FrontMatterExtra],
    single_package_flat: bool = False,
):
    print("Generating package folders")

    for pkg in packages:
        if len(classes[pkg["name"]]) == 0 and len(pkg["methods"]) == 0 and len(pkg["variables"]) == 0:
            # Skip packages with no classes, methods, or variables
            continue

        append_to_landing = False
        if single_package_flat:
            # DOC-1335: the section holds exactly this one package, so the module
            # level is collapsed -- the module body (doc, directory, methods,
            # variables) is APPENDED to the landing page generate_home already
            # wrote, and class pages sit at the section root beside it.
            pkg_index = os.path.join(pkg_root, "_index.md")
            append_to_landing = True
        elif flatten:
            pkg_index = os.path.join(pkg_root, f"{pkg['name']}.md")
            frontmatter_extra = None
        else:
            pkg_folder = os.path.join(pkg_root, pkg["name"])
            if not os.path.isdir(pkg_folder):
                os.mkdir(pkg_folder)
            pkg_index = os.path.join(pkg_folder, "_index.md")

        # print(f"Generating package index for {pkg['name']}")
        with open(pkg_index, "a" if append_to_landing else "w") as index:
            if not append_to_landing:
                write_front_matter(pkg["name"], index, frontmatter_extra)
                index.write(f"# {pkg['name']}\n\n")

            doc = pkg["doc"] if "doc" in pkg else ""
            if doc:
                doc = inspect.cleandoc(doc)
                doc = re.sub(r'^(\s*Examples?)::', r'\1:', doc, flags=re.MULTILINE)
                index.write(f"{doc}\n")

            classDefs = classes[pkg["name"]]

            index.write("## Directory\n\n")

            generate_classes_and_error_list(
                pkg=pkg,
                clss=classDefs,
                output=index,
                pkg_root=pkg_root,
                doc_level=3,
                relative_to_file=pkg_index,
                flatten=flatten,
                ignore_types=ignore_types,
                single_package_flat=single_package_flat,
            )

            if len(pkg["methods"]) > 0:
                generate_method_list(pkg["methods"], output=index, doc_level=3)

            if len(pkg["variables"]) > 0:
                index.write("### Variables\n\n")
                generate_props(pkg["variables"], output=index)

            if len(pkg["methods"]) > 0:
                index.write("## Methods\n\n")

                for method in pkg["methods"]:
                    generate_method(method, output=index, doc_level=3)

            if flatten:
                for cls, clsInfo in classes[pkg["name"]].items():
                    if cls in ignore_types:
                        continue

                    index.write(f"## {cls}\n\n")

                    generate_class_details(
                        info=clsInfo,
                        output=index,
                        doc_level=3,
                    )
