"""Utilities for handling Pydantic BaseModel classes in API doc generation."""

import inspect
from typing import Any


def is_pydantic_model(cls: type | None) -> bool:
    """Check if a class is a Pydantic BaseModel."""
    if cls is None:
        return False
    try:
        from pydantic import BaseModel
        return issubclass(cls, BaseModel)
    except ImportError:
        return False


def get_pydantic_excluded_members(cls: type) -> set[str]:
    """Return the set of public member names defined on pydantic.BaseModel itself.

    These are framework members (model_validate, model_dump, model_extra, etc.)
    that should not appear in user-facing API docs. Members that a user-defined
    class or its user-defined parents override are NOT excluded — only names
    whose definition lives on BaseModel or its internal bases.
    """
    try:
        from pydantic import BaseModel
    except ImportError:
        return set()

    # Collect all public members defined directly on BaseModel (methods + properties).
    basemodel_members = {
        name for name in dir(BaseModel)
        if not name.startswith("_")
    }

    # Don't exclude members that the user's class (or a user-defined ancestor)
    # has overridden. Walk the MRO and check if any class before BaseModel
    # defines the member in its own __dict__.
    overridden = set()
    for ancestor in inspect.getmro(cls):
        if ancestor is BaseModel:
            break
        for name in ancestor.__dict__:
            if name in basemodel_members:
                overridden.add(name)

    return basemodel_members - overridden


def get_pydantic_init_fields(cls: type) -> list[dict[str, Any]]:
    """Extract constructor field info from a Pydantic model's model_fields.

    Returns a list of dicts with keys: name, type, default, description.
    """
    from lib.parser.methods import _sanitize_type_str

    fields = []
    for field_name, field_info in cls.model_fields.items():
        field_type = ""
        if field_info.annotation is not None:
            field_type = _sanitize_type_str(str(field_info.annotation))

        default = None
        try:
            from pydantic_core import PydanticUndefined
            if field_info.default is not None and field_info.default is not PydanticUndefined:
                default = str(field_info.default)
            elif field_info.default_factory is not None:
                default = f"{field_info.default_factory.__name__}()"
        except ImportError:
            if field_info.default is not None:
                default = str(field_info.default)

        fields.append({
            "name": field_name,
            "type": field_type,
            "default": default,
            "description": field_info.description,
        })
    return fields


def build_pydantic_init_signature(cls: type) -> str:
    """Build a human-readable signature string from Pydantic model_fields."""
    from lib.parser.methods import _sanitize_type_str

    parts = []
    for field_name, field_info in cls.model_fields.items():
        type_str = ""
        if field_info.annotation is not None:
            type_str = f": {_sanitize_type_str(str(field_info.annotation))}"

        default_str = ""
        try:
            from pydantic_core import PydanticUndefined
            if field_info.default is not None and field_info.default is not PydanticUndefined:
                default_str = f" = {field_info.default!r}"
            elif field_info.default_factory is not None:
                default_str = f" = {field_info.default_factory.__name__}()"
        except ImportError:
            if field_info.default is not None:
                default_str = f" = {field_info.default!r}"

        parts.append(f"{field_name}{type_str}{default_str}")
    return f"({', '.join(parts)})"
