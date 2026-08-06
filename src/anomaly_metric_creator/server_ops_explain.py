"""Pure ``kubectl explain`` and OpenAPI schema formatters.

Epic step 6a of the ``server_ops.py`` decomposition (task
``08-05-server-ops-explain-payload-extract``). These ten helpers turn a
projected Kubernetes object into an OpenAPI-shaped schema and render the
``kubectl explain`` text form.

They are entirely free of simulation state: the closure audit found no
reference to ``SimulationState`` or ``resource_snapshot``, and no
intra-package import at all. ``_render_explain`` and
``_explain_schema_for_kind`` stay in ``server_ops`` (they bind state) and
call into here through the re-import stub at this block's original
position.
"""

from __future__ import annotations

from typing import Any


def _openapi_schema_from_value(
    value: Any,
    *,
    root_kind: str,
    path: tuple[str, ...],
    description: str = "",
) -> dict[str, Any]:
    field_description = description or _explain_field_description(root_kind, path)
    if isinstance(value, bool):
        return {"type": "boolean", "description": field_description}
    if isinstance(value, int):
        return {"type": "integer", "format": "int32", "description": field_description}
    if isinstance(value, float):
        return {"type": "number", "format": "double", "description": field_description}
    if isinstance(value, dict):
        return {
            "type": "object",
            "title": _explain_title(root_kind, path),
            "description": field_description,
            "properties": {
                str(key): _openapi_schema_from_value(item, root_kind=root_kind, path=(*path, str(key)))
                for key, item in value.items()
            },
        }
    if isinstance(value, list):
        item_value = value[0] if value else {}
        return {
            "type": "array",
            "description": field_description,
            "items": _openapi_schema_from_value(item_value, root_kind=root_kind, path=(*path, "items")),
        }
    return {"type": "string", "description": field_description}


def _explain_field_description(root_kind: str, path: tuple[str, ...]) -> str:
    if not path:
        return f"{root_kind} schema projected from the AMC simulator Kubernetes facade."
    dotted = ".".join(part for part in path if part != "items")
    return f"{dotted} field projected from AMC's simulator-backed {root_kind} object."


def _explain_title(root_kind: str, path: tuple[str, ...]) -> str:
    if not path:
        return root_kind
    if path[-1] == "metadata":
        return "ObjectMeta"
    words = [root_kind, *(part for part in path if part != "items")]
    return "".join(word[:1].upper() + word[1:] for word in words if word)


def _explain_schema_at_path(schema: dict[str, Any], field_path: str) -> dict[str, Any] | None:
    node = schema
    for part in [item for item in field_path.split(".") if item]:
        node = _explain_display_schema(node)
        properties = node.get("properties")
        if not isinstance(properties, dict) or part not in properties:
            return None
        child = properties[part]
        if not isinstance(child, dict):
            return None
        node = child
    return node


def _format_explain(
    schema_info: dict[str, Any],
    field_path: str,
    field_schema: dict[str, Any],
    recursive: bool,
) -> str:
    lines = [
        f"KIND:       {schema_info['kind']}",
        f"VERSION:    {schema_info['api_version']}",
        "",
    ]
    if field_path:
        lines.extend([
            f"FIELD:      {field_path} {_explain_type_label(field_schema)}",
            "",
        ])
    lines.extend([
        "DESCRIPTION:",
        "    " + str(field_schema.get("description", "")).strip(),
        "",
    ])
    properties = _explain_properties(field_schema)
    if properties:
        lines.append("FIELDS:")
        if recursive:
            lines.extend(_format_recursive_explain_fields(properties, depth=1, max_depth=5))
        else:
            for name, child in properties.items():
                lines.append(f"  {name:<20} {_explain_type_label(child)}")
        lines.append("")
    return "\n".join(lines)


def _format_recursive_explain_fields(
    properties: dict[str, Any],
    *,
    depth: int,
    max_depth: int,
) -> list[str]:
    lines: list[str] = []
    indent = "  " * depth
    for name, child in properties.items():
        if not isinstance(child, dict):
            continue
        lines.append(f"{indent}{name:<20} {_explain_type_label(child)}")
        if depth >= max_depth:
            continue
        child_properties = _explain_properties(child)
        if child_properties:
            lines.extend(
                _format_recursive_explain_fields(
                    child_properties,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            )
    return lines


def _explain_properties(schema: dict[str, Any]) -> dict[str, Any]:
    display_schema = _explain_display_schema(schema)
    properties = display_schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def _explain_display_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return items
    return schema


def _explain_type_label(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items")
        item_label = _explain_type_name(items) if isinstance(items, dict) else "Object"
        return f"<[]{item_label}>"
    return f"<{_explain_type_name(schema)}>"


def _explain_type_name(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if schema_type == "object":
        title = schema.get("title")
        return str(title) if title else "Object"
    if schema_type == "integer":
        return "integer"
    if schema_type == "number":
        return "number"
    if schema_type == "boolean":
        return "boolean"
    return "string"
