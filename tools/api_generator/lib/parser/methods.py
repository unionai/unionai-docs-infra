import inspect
import re
from functools import cached_property
from typing import Optional, Any
from lib.parser.docstring import parse_docstring
from lib.ptypes import MethodInfo, PropertyInfo, VariableInfo, FrameworkType, ParamInfo

# Pattern: <some.module.ClassName object at 0x7f...>
_OBJECT_REPR_RE = re.compile(r"<([\w.]+)\s+object\s+at\s+0x[0-9a-fA-F]+>")
# Pattern: <module 'some.module' from '/path/to/file.py'>
_MODULE_REPR_RE = re.compile(r"<module '([\w.]+)' from '[^']+'>")


def _sanitize_type_str(s: str) -> str:
    """Replace object/module repr strings (with memory addresses or paths) with just the name."""
    s = _MODULE_REPR_RE.sub(r"\1", s)
    return _OBJECT_REPR_RE.sub(r"\1", s)


def parse_method(
    name: str, member: object, parent_name: str | None = None, cls: type | None = None
) -> Optional[MethodInfo]:
    from lib.parser.syncify import is_syncify_method
    framework: FrameworkType = "python"
    if is_syncify_method(name, member):
        framework = "syncify"
        if parent_name and inspect.isfunction(getattr(member, "fn")):
            parent_name = f"<{parent_name} instance>"
    elif not (inspect.isfunction(member) or inspect.ismethod(member)):
        return None

    return do_parse_method(name, member, framework, parent_name, cls=cls)


def _is_pydantic_model(cls: type | None) -> bool:
    """Check if a class is a Pydantic BaseModel."""
    if cls is None:
        return False
    try:
        from pydantic import BaseModel

        return issubclass(cls, BaseModel)
    except ImportError:
        return False


def _build_pydantic_init_params(cls: type) -> list[ParamInfo]:
    """Extract constructor params from Pydantic model_fields instead of inspect.signature."""
    params = []
    for field_name, field_info in cls.model_fields.items():
        field_type = ""
        if field_info.annotation is not None:
            field_type = _sanitize_type_str(str(field_info.annotation))

        default = None
        if field_info.default is not None:
            from pydantic_core import PydanticUndefined

            if field_info.default is not PydanticUndefined:
                default = str(field_info.default)
        elif field_info.default_factory is not None:
            default = f"{field_info.default_factory.__name__}()"

        params.append(
            ParamInfo(
                name=field_name,
                default=default,
                kind="KEYWORD_ONLY",
                type=field_type,
                doc=field_info.description,
            )
        )
    return params


def _build_pydantic_init_signature(cls: type) -> str:
    """Build a human-readable signature string from Pydantic model_fields."""
    parts = []
    for field_name, field_info in cls.model_fields.items():
        type_str = ""
        if field_info.annotation is not None:
            type_str = f": {_sanitize_type_str(str(field_info.annotation))}"

        default_str = ""
        if field_info.default is not None:
            from pydantic_core import PydanticUndefined

            if field_info.default is not PydanticUndefined:
                default_str = f" = {field_info.default!r}"
        elif field_info.default_factory is not None:
            default_str = f" = {field_info.default_factory.__name__}()"

        parts.append(f"{field_name}{type_str}{default_str}")
    return f"({', '.join(parts)})"


def do_parse_method(
    name: str,
    member: Any,
    framework: FrameworkType,
    parent_name: str | None = None,
    cls: type | None = None,
) -> Optional[MethodInfo]:
    doc_info = parse_docstring(inspect.getdoc(member), source=member)
    docstr = doc_info["docstring"] if doc_info else None
    params_docs = doc_info["params"] if doc_info else None

    # For Pydantic __init__, extract params from model_fields
    if name == "__init__" and _is_pydantic_model(cls):
        params = _build_pydantic_init_params(cls)
        sig_str = _build_pydantic_init_signature(cls)
        return_type = "None"
    else:
        sig = inspect.signature(member)
        param_types = {
            name: (
                param.annotation
                if str(param.annotation) != "<class 'inspect._empty'>"
                else ""
            )
            for name, param in sig.parameters.items()
        }
        return_type = (
            sig.return_annotation
            if str(sig.return_annotation) != "<class 'inspect._empty'>"
            else "None"
        )
        sig_str = _sanitize_type_str(str(sig))
        params = [
            ParamInfo(
                name=param.name,
                default=(
                    str(param.default)
                    if param.default != inspect.Parameter.empty
                    else None
                ),
                kind=str(param.kind),
                type=_sanitize_type_str(str(param_types[param.name])),
                doc=None,
            )
            for param in sig.parameters.values()
        ]

    return_doc = (
        doc_info["return_doc"]
        if doc_info is not None
        and "return_doc" in doc_info
        and doc_info["return_doc"] is not None
        else None
    )

    method_info = MethodInfo(
        name=name,
        doc=docstr,
        signature=sig_str,
        params=params,
        params_doc=params_docs,
        return_type=_sanitize_type_str(str(return_type)),
        return_doc=return_doc,
        framework=framework,
        parent_name=parent_name,
    )
    return method_info


def parse_property(name: str, member: object) -> Optional[PropertyInfo]:
    if not isinstance(member, (property, cached_property)):
        return None

    doc_info = parse_docstring(inspect.getdoc(member), source=member)
    docstr = doc_info["docstring"] if doc_info else None
    property_info = PropertyInfo(
        name=name,
        doc=docstr,
        type=None
    )
    return property_info


def parse_variable(name: str, member: object) -> Optional[VariableInfo]:
    mtype = type(member).__name__
    if mtype == "module":
        return None

    var_info = VariableInfo(
        name=name,
        type=mtype,
        doc=None
    )

    return var_info
