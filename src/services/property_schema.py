"""属性 Schema 服务层 — 类型校验、优先级解析"""
import re
from dataclasses import dataclass, field
from typing import cast

from src.models.property_schema import PropertySchema
from src.repositories.property_schema_repo import PropertySchemaRepository
from src.services.db import Database


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class PropertySchemaService:
    TYPE_NAMES = {"text", "number", "date", "datetime", "boolean", "url", "node_ref"}

    def __init__(self, db=None, repo=None):
        self._db = db or Database
        self._repo = repo or PropertySchemaRepository(db=self._db)

    def upsert(self, schema: PropertySchema) -> PropertySchema:
        if schema.property_type not in self.TYPE_NAMES:
            raise ValueError(
                f"Unsupported property_type '{schema.property_type}', "
                f"must be one of {sorted(self.TYPE_NAMES)}"
            )
        return cast(PropertySchema, self._repo.upsert(schema))

    def resolve_schema(
        self,
        property_name: str,
        page_id: str = "",
        tags: list[str] | None = None,
        block_id: str = "",
    ) -> PropertySchema | None:
        # 优先级从高到低: block(3) -> page(2) -> tag(1) -> global(0)
        if block_id:
            found = self._repo.find("block", block_id, property_name)
            if found:
                return cast(PropertySchema, found)
        if page_id:
            found = self._repo.find("page", page_id, property_name)
            if found:
                return cast(PropertySchema, found)
        if tags:
            for tag in tags:
                found = self._repo.find("tag", tag, property_name)
                if found:
                    return cast(PropertySchema, found)
        return cast(PropertySchema | None, self._repo.find("global", "", property_name))

    def validate_value(
        self,
        property_name: str,
        value: object,
        scope_type: str,
        scope_id: str = "",
    ) -> ValidationResult:
        schema = self._repo.find(scope_type, scope_id, property_name)
        if schema is None:
            return ValidationResult(valid=False, errors=[f"No schema for '{property_name}'"])

        errors: list[str] = []

        # 类型检查
        if not self._matches_type(value, schema.property_type):
            errors.append(
                f"Value {value!r} does not match type '{schema.property_type}'"
            )

        # choices 约束
        if schema.choices is not None and value not in schema.choices:
            errors.append(
                f"Value {value!r} not in allowed choices {schema.choices}"
            )

        return ValidationResult(valid=len(errors) == 0, errors=errors)

    @staticmethod
    def _matches_type(value: object, property_type: str) -> bool:
        if property_type == "text":
            return isinstance(value, str)
        if property_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if property_type == "date":
            return isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
        if property_type == "datetime":
            return isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value))
        if property_type == "boolean":
            return isinstance(value, bool)
        if property_type == "url":
            return isinstance(value, str) and (value.startswith("http://") or value.startswith("https://"))
        if property_type == "node_ref":
            return isinstance(value, str) and len(value) > 0
        return False
