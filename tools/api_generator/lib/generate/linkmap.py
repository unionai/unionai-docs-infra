from typing import List

import json
import os
import sys

from lib.generate.helper import generate_anchor_from_name
from lib.ptypes import ClassPackageMap, PackageInfo


def generate_linkmap_metadata(
    packages: List[PackageInfo],
    classes: ClassPackageMap,
    pkg_root: str,
    api_name: str,
    include_short_names: bool = False,
    flatten: bool = False,
):
    # Skip the content root (remove first path component: content/a/b/c -> a/b/c)
    site_root = "/".join(pkg_root.split("/")[1:])

    # Build packages metadata from the packages list
    packages_dict = {pkg["name"]: f"/{site_root}/{pkg['name']}/" for pkg in packages}

    # Build methods metadata. Methods are only emitted under their fully
    # qualified name — short method names like `init`, `run`, `log` are too
    # generic to autolink safely.
    methods_dict = {}
    for pkg in packages:
        for m in pkg["methods"]:
            url = f"/{site_root}/{pkg['name']}/#{m['name']}"
            methods_dict[f"{pkg['name']}.{m['name']}"] = url

    # Build identifiers metadata from classes. When emitting short names, a
    # collision (same short name from two different qualified paths) is
    # resolved by shortest-qualified-prefix-wins.
    identifiers_dict = {}
    short_winners = {}  # short_name -> (full_name, depth)
    collisions = []     # (short, winner_full, loser_full)

    for pkg in classes:
        for clz in classes[pkg]:
            if flatten:
                url = f"/{site_root}/{pkg}/#{generate_anchor_from_name(clz)}"
            else:
                url = f"/{site_root}/{pkg}/{clz.split('.')[-1].lower()}/"
            identifiers_dict[clz] = url

            if include_short_names:
                short_name = clz.split('.')[-1]
                depth = clz.count('.')
                existing = short_winners.get(short_name)
                if existing is None:
                    identifiers_dict[short_name] = url
                    short_winners[short_name] = (clz, depth)
                else:
                    existing_full, existing_depth = existing
                    if depth < existing_depth:
                        identifiers_dict[short_name] = url
                        short_winners[short_name] = (clz, depth)
                        collisions.append((short_name, clz, existing_full))
                    else:
                        # Existing wins (stable on tie)
                        collisions.append((short_name, existing_full, clz))

    if collisions:
        print(
            f"[linkmap:{api_name}] {len(collisions)} short-name collision(s); "
            "shortest qualified prefix wins:",
            file=sys.stderr,
        )
        for short, winner, loser in collisions:
            print(f"  {short!r}: kept {winner} | skipped {loser}", file=sys.stderr)

    metadata = {
        "packages": packages_dict,
        "identifiers": identifiers_dict,
        "methods": methods_dict
    }

    os.makedirs("linkmap", exist_ok=True)
    with open(f"linkmap/{api_name}-linkmap.json", "w") as file:
        json.dump(metadata, file, indent=2)
