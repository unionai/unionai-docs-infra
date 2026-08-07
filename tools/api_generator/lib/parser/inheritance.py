"""
Keep third-party base-class surface out of the generated API reference.

``inspect.getmembers`` walks the entire MRO, so a thin wrapper over a
third-party base is documented as though that base's whole public API were
ours. ``flyteplugins.agents.deepagents.DurableChatModel`` wraps LangChain's
``BaseChatModel``; without this filter its page carries ~54 methods, nearly all
of them ``Runnable``/``BaseChatModel`` internals -- including a ``dict()`` whose
docstring tells the reader it is removed in ``langchain-core`` 2.0. The handful
of methods the class actually defines are lost in the middle of it.

The rule: document a member when the class that *defines* it is first-party.
A member inherited from a first-party base still counts -- only genuinely
foreign bases (``langchain_core``, ``pydantic``, ``enum``, ``builtins``, ...)
are filtered out.
"""

import inspect
from typing import Optional

# Namespace roots whose members belong in these docs. The documented class's
# own root is always added, so this only needs to name the *other* first-party
# namespaces a class might inherit from (a plugin subclassing an SDK base).
FIRST_PARTY_ROOTS = frozenset({"flyte", "flyteplugins", "union", "unionai"})


def _root_package(module_name: Optional[str]) -> str:
    return (module_name or "").split(".")[0]


def defining_class(cls: type, name: str) -> Optional[type]:
    """The class in ``cls``'s MRO that actually defines ``name``."""
    try:
        mro = inspect.getmro(cls)
    except (AttributeError, TypeError):
        return None
    for ancestor in mro:
        if name in getattr(ancestor, "__dict__", {}):
            return ancestor
    return None


def first_party_roots(cls: type) -> frozenset:
    """Roots considered first-party when documenting ``cls``."""
    return FIRST_PARTY_ROOTS | {_root_package(getattr(cls, "__module__", ""))}


def is_foreign_member(cls: type, name: str) -> bool:
    """
    True when ``name`` reaches ``cls`` only through a third-party base class.

    Falls back to False (document it) whenever the owner cannot be determined,
    so a member supplied dynamically is never dropped on a guess.
    """
    owner = defining_class(cls, name)
    if owner is None:
        return False
    return _root_package(getattr(owner, "__module__", None)) not in first_party_roots(cls)


def test_is_foreign_member():
    class ThirdPartyBase:
        __module__ = "langchain_core.language_models"

        def abatch(self): ...
        def bind_tools(self): ...

    class FirstPartyBase:
        __module__ = "flyte.ai.agents"

        def shared_helper(self): ...

    class Documented(FirstPartyBase, ThirdPartyBase):
        __module__ = "flyteplugins.agents.deepagents._model"

        def bind_tools(self):  # overrides the third-party base
            ...

        def own_method(self): ...

    # Defined here -> documented.
    assert not is_foreign_member(Documented, "own_method")
    # Overridden here -> documented, even though the name exists upstream.
    assert not is_foreign_member(Documented, "bind_tools")
    # Inherited from a first-party base -> documented.
    assert not is_foreign_member(Documented, "shared_helper")
    # Inherited from a third-party base -> filtered out.
    assert is_foreign_member(Documented, "abatch")
    # Unknown names are kept rather than guessed away.
    assert not is_foreign_member(Documented, "does_not_exist")

    # Real third-party bases: enum and builtins are foreign to a Flyte class.
    import enum

    class Colour(enum.Enum):
        __module__ = "flyte.types"
        RED = 1

    assert is_foreign_member(Colour, "name")
    assert is_foreign_member(Colour, "value")

    # A class documented from a third-party root keeps its own members.
    assert not is_foreign_member(ThirdPartyBase, "abatch")
    print("test_is_foreign_member: ok")


if __name__ == "__main__":
    test_is_foreign_member()
