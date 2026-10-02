"""Decide which public-in-code names stay out of the API reference.

Python has no "public but undocumented" visibility, so a library marks such names
in the docstring. The generator honors Sphinx autodoc's `:meta private:` field on
a class, method, property or function:

    def schema_match(cls, incoming: dict):
        \"\"\"Check if incoming schema matches File schema.

        :meta private:
        \"\"\"

It also honors the legacy convention flyte-sdk used before adopting the field: a
docstring that opens with `Internal:`. Drop that rule once flyte-sdk has switched.

Parameters follow the same rule as names: a `_`-prefixed parameter (such as
`with_runcontext(_tracker=...)`) is private and is left out of the signature and
the parameter table.
"""

import inspect
import re
from functools import cached_property
from typing import Any

# A Sphinx `:meta <field>:` line, on its own line anywhere in the docstring.
META_FIELD_RE = re.compile(r"^[ \t]*:meta[ \t]+([a-z-]+):[ \t]*$", re.MULTILINE)
LEGACY_INTERNAL_RE = re.compile(r"^\s*Internal:")


def is_private_doc(doc: str | None) -> bool:
    if not doc:
        return False
    if "private" in META_FIELD_RE.findall(doc):
        return True
    return bool(LEGACY_INTERNAL_RE.match(doc))


def is_private_member(member: Any) -> bool:
    """True when a class, function, method or property is marked private.

    Only these kinds are checked: for any other value, `inspect.getdoc` returns the
    docstring of the value's type, which says nothing about the attribute itself.
    """
    if isinstance(member, (property, cached_property)):
        doc = inspect.getdoc(member)
    elif inspect.isclass(member):
        # The class's own docstring, not one inherited from a base.
        doc = member.__doc__
    elif callable(member):
        doc = inspect.getdoc(member)
    else:
        return False
    return is_private_doc(doc)


def strip_meta_fields(doc: str) -> str:
    """Remove `:meta ...:` lines, which are directives, not prose."""
    return META_FIELD_RE.sub("", doc).rstrip()


def is_private_param(name: str) -> bool:
    return name.startswith("_")


def public_signature(sig: inspect.Signature) -> inspect.Signature:
    """`sig` without its private parameters.

    Dropping parameters never breaks the ordering rules `Signature` enforces.
    """
    return sig.replace(
        parameters=[p for p in sig.parameters.values() if not is_private_param(p.name)]
    )
