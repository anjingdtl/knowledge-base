"""Teach mypy about Database's metaclass-based singleton delegation."""
from __future__ import annotations

from collections.abc import Callable

from mypy.plugin import AttributeContext, Plugin
from mypy.typeops import bind_self
from mypy.types import FunctionLike, Type, get_proper_type

_DATABASE_ATTRIBUTE_PREFIX = "src.services.db.Database."
_NATIVE_CLASS_ATTRIBUTES = frozenset({
    "connect",
    "open_runtime",
    "_open_readonly_runtime",
})


def _bind_database_method(context: AttributeContext) -> Type:
    attribute_type = get_proper_type(context.default_attr_type)
    if isinstance(attribute_type, FunctionLike):
        return bind_self(attribute_type)
    return context.default_attr_type


class DatabaseMetaPlugin(Plugin):
    """Model Database.method() as a call delegated to Database._instance."""

    def get_class_attribute_hook(
        self,
        fullname: str,
    ) -> Callable[[AttributeContext], Type] | None:
        if not fullname.startswith(_DATABASE_ATTRIBUTE_PREFIX):
            return None
        attribute_name = fullname.removeprefix(_DATABASE_ATTRIBUTE_PREFIX)
        if attribute_name in _NATIVE_CLASS_ATTRIBUTES:
            return None
        return _bind_database_method


def plugin(version: str) -> type[Plugin]:
    return DatabaseMetaPlugin
