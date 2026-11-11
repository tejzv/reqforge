from __future__ import annotations

from typing import Any

from reqforge.models import JsonSchemaNode, SchemaField
from reqforge.utils import is_email, is_iso_datetime, is_url, is_uuid, singularize, to_pascal_case, to_snake_case


def _infer_string_format(value: str) -> str | None:
    if is_uuid(value):
        return "uuid"
    if is_iso_datetime(value):
        return "date-time"
    if is_email(value):
        return "email"
    if is_url(value):
        return "uri"
    return None


def _node_from_value(value: Any) -> JsonSchemaNode:
    if value is None:
        return JsonSchemaNode(kind="null", example=None)
    if isinstance(value, bool):
        return JsonSchemaNode(kind="boolean", example=value)
    if isinstance(value, int):
        return JsonSchemaNode(kind="integer", example=value)
    if isinstance(value, float):
        return JsonSchemaNode(kind="number", example=value)
    if isinstance(value, str):
        return JsonSchemaNode(
            kind="string",
            format_hint=_infer_string_format(value),
            example=value,
        )
    if isinstance(value, list):
        if not value:
            return JsonSchemaNode(kind="array", items=JsonSchemaNode(kind="any"), example=[])
        merged_item = _node_from_value(value[0])
        for elem in value[1:]:
            merged_item = merge_schema_nodes(merged_item, _node_from_value(elem))
        return JsonSchemaNode(kind="array", items=merged_item, example=value[:2])
    if isinstance(value, dict):
        fields: dict[str, SchemaField] = {}
        seen_names: set[str] = set()
        for raw_key, val in value.items():
            wire_name = str(raw_key)
            py_name = to_snake_case(wire_name)
            while py_name in seen_names:
                py_name = f"{py_name}_"
            seen_names.add(py_name)
            child = _node_from_value(val)
            nullable = child.kind == "null"
            fields[wire_name] = SchemaField(
                name=py_name,
                wire_name=wire_name,
                node=child,
                required=True,
                nullable=nullable,
            )
        return JsonSchemaNode(kind="object", fields=fields, example=value)
    return JsonSchemaNode(kind="any", example=value)


def _flatten_variants(node: JsonSchemaNode) -> list[JsonSchemaNode]:
    if node.kind == "union":
        return list(node.variants)
    return [node]


def merge_schema_nodes(a: JsonSchemaNode, b: JsonSchemaNode) -> JsonSchemaNode:
    if a.kind == "any":
        return b
    if b.kind == "any":
        return a

    if a.kind == "null" and b.kind != "null":
        if b.kind == "union":
            if not any(v.kind == "null" for v in b.variants):
                return JsonSchemaNode(kind="union", variants=[*b.variants, a])
            return b
        return JsonSchemaNode(kind="union", variants=[b, a])

    if b.kind == "null" and a.kind != "null":
        if a.kind == "union":
            if not any(v.kind == "null" for v in a.variants):
                return JsonSchemaNode(kind="union", variants=[*a.variants, b])
            return a
        return JsonSchemaNode(kind="union", variants=[a, b])

    if a.kind == b.kind:
        if a.kind == "string":
            fmt = a.format_hint if a.format_hint == b.format_hint else None
            return JsonSchemaNode(kind="string", format_hint=fmt, example=a.example or b.example)
        if a.kind in {"integer", "number", "boolean", "null"}:
            return JsonSchemaNode(kind=a.kind, example=a.example if a.example is not None else b.example)
        if a.kind == "array":
            item_a = a.items or JsonSchemaNode(kind="any")
            item_b = b.items or JsonSchemaNode(kind="any")
            return JsonSchemaNode(
                kind="array",
                items=merge_schema_nodes(item_a, item_b),
                example=a.example or b.example,
            )
        if a.kind == "object":
            merged_fields: dict[str, SchemaField] = {}
            used_field_names: set[str] = set()
            all_keys = list(a.fields.keys())
            for k in b.fields:
                if k not in a.fields:
                    all_keys.append(k)
            for wire_name in all_keys:
                fa = a.fields.get(wire_name)
                fb = b.fields.get(wire_name)
                if fa is not None and fb is not None:
                    merged_child = merge_schema_nodes(fa.node, fb.node)
                    nullable = fa.nullable or fb.nullable or _node_allows_null(merged_child)
                    clean_child = _strip_null_from_union(merged_child) if nullable else merged_child
                    fname = fa.name
                    while fname in used_field_names:
                        fname = f"{fname}_"
                    used_field_names.add(fname)
                    merged_fields[wire_name] = SchemaField(
                        name=fname,
                        wire_name=wire_name,
                        node=clean_child,
                        required=fa.required and fb.required,
                        nullable=nullable,
                    )
                else:
                    existing = fa if fa is not None else fb
                    assert existing is not None
                    nullable = existing.nullable or existing.node.kind == "null"
                    fname = existing.name
                    while fname in used_field_names:
                        fname = f"{fname}_"
                    used_field_names.add(fname)
                    merged_fields[wire_name] = SchemaField(
                        name=fname,
                        wire_name=wire_name,
                        node=existing.node,
                        required=False,
                        nullable=nullable,
                    )
            return JsonSchemaNode(kind="object", fields=merged_fields, example=a.example or b.example)

    if {a.kind, b.kind} == {"integer", "number"}:
        return JsonSchemaNode(kind="number", example=a.example if a.example is not None else b.example)

    variants: list[JsonSchemaNode] = []
    for candidate in _flatten_variants(a) + _flatten_variants(b):
        merged = False
        for idx, existing in enumerate(variants):
            if existing.kind == candidate.kind and existing.kind in {"object", "array", "string", "integer", "number", "boolean", "null"}:
                variants[idx] = merge_schema_nodes(existing, candidate)
                merged = True
                break
        if not merged:
            variants.append(candidate)

    if len(variants) == 1:
        return variants[0]
    return JsonSchemaNode(kind="union", variants=variants)


def _node_allows_null(node: JsonSchemaNode) -> bool:
    if node.kind == "null":
        return True
    if node.kind == "union":
        return any(v.kind == "null" for v in node.variants)
    return False


def _strip_null_from_union(node: JsonSchemaNode) -> JsonSchemaNode:
    if node.kind != "union":
        return node
    non_null = [v for v in node.variants if v.kind != "null"]
    if not non_null:
        return JsonSchemaNode(kind="null")
    if len(non_null) == 1:
        return non_null[0]
    return JsonSchemaNode(kind="union", variants=non_null)


def _normalize_object_nullables(node: JsonSchemaNode) -> None:
    if node.kind == "object":
        for f in node.fields.values():
            if _node_allows_null(f.node):
                f.nullable = True
                f.node = _strip_null_from_union(f.node)
            _normalize_object_nullables(f.node)
    elif node.kind == "array" and node.items is not None:
        _normalize_object_nullables(node.items)
    elif node.kind == "union":
        for v in node.variants:
            _normalize_object_nullables(v)


def _assign_model_names(
    node: JsonSchemaNode,
    suggested_name: str,
    used_names: set[str],
    is_array_item: bool = False,
    parent_prefix: str = "",
) -> None:
    if node.kind == "object" and node.fields:
        base = to_pascal_case(singularize(suggested_name) if is_array_item else suggested_name)
        if not base:
            base = "Model"
        candidate = base
        if candidate in used_names and parent_prefix:
            candidate = f"{parent_prefix}{base}"
        stem = candidate
        counter = 2
        while candidate in used_names:
            candidate = f"{stem}{counter}"
            counter += 1
        used_names.add(candidate)
        node.model_name = candidate
        for wire_name, fld in node.fields.items():
            _assign_model_names(
                fld.node,
                to_pascal_case(wire_name),
                used_names,
                is_array_item=False,
                parent_prefix=candidate,
            )
    elif node.kind == "array" and node.items is not None:
        _assign_model_names(
            node.items,
            suggested_name,
            used_names,
            is_array_item=True,
            parent_prefix=parent_prefix,
        )
    elif node.kind == "union":
        for idx, variant in enumerate(node.variants, start=1):
            _assign_model_names(
                variant,
                f"{suggested_name}Variant{idx}",
                used_names,
                is_array_item=is_array_item,
                parent_prefix=parent_prefix,
            )


def infer_schema_from_samples(
    samples: list[Any],
    *,
    root_name: str = "Payload",
    used_names: set[str] | None = None,
) -> JsonSchemaNode | None:
    valid = [s for s in samples if s is not None]
    if not valid:
        return None
    merged = _node_from_value(valid[0])
    for sample in valid[1:]:
        merged = merge_schema_nodes(merged, _node_from_value(sample))
    _normalize_object_nullables(merged)
    names = used_names if used_names is not None else set()
    _assign_model_names(merged, root_name, names)
    return merged


def collect_named_models(node: JsonSchemaNode | None) -> list[JsonSchemaNode]:
    if node is None:
        return []
    ordered: list[JsonSchemaNode] = []
    visited_ids: set[int] = set()

    def walk(cur: JsonSchemaNode) -> None:
        if id(cur) in visited_ids:
            return
        visited_ids.add(id(cur))
        if cur.kind == "object" and cur.fields:
            for fld in cur.fields.values():
                walk(fld.node)
            if cur.model_name:
                ordered.append(cur)
        elif cur.kind == "array" and cur.items is not None:
            walk(cur.items)
        elif cur.kind == "union":
            for v in cur.variants:
                walk(v)

    walk(node)
    return ordered
