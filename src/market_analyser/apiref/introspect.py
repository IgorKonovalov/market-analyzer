"""Introspect the live sidecar surfaces into ordered, structured records
(Plan 0070 phase 1, ADR-0064).

Pure functions: given the fully-wired `FastMCP` server, the `FastAPI` app, and
the event registry (all from `wiring.py`), return deterministic tuples of frozen
records — `ToolDoc`, `RouteDoc`, `EventDoc`. Nothing here re-derives a schema by
hand: parameter types, defaults, required-ness, and return shapes come straight
from the JSON schemas FastMCP / Pydantic / FastAPI already computed. The records
sort deterministically (tools by name, routes by path then method, events by
kind) so the phase-2 renderer's output is stable across runs and machines.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from market_analyser.events import TYPE_REGISTRY

_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})


@dataclass(frozen=True)
class ParamDoc:
    """One parameter / field, rendered from a JSON-schema node."""

    name: str
    type_str: str
    required: bool
    default: str | None


@dataclass(frozen=True)
class ToolDoc:
    """One MCP tool, from the FastMCP tool registry."""

    name: str
    summary: str
    description: str
    params: tuple[ParamDoc, ...]
    return_shape: str
    return_fields: tuple[ParamDoc, ...]
    source_module: str
    source_path: str


@dataclass(frozen=True)
class RouteDoc:
    """One REST operation, from the FastAPI OpenAPI document."""

    path: str
    method: str
    summary: str
    description: str
    auth: str
    params: tuple[ParamDoc, ...]
    request_schema: str | None
    response_schema: str | None


@dataclass(frozen=True)
class EventDoc:
    """One SSE envelope kind, from the event `TYPE_REGISTRY`."""

    kind: str
    version: int
    summary: str
    description: str
    payload_fields: tuple[ParamDoc, ...]
    source_model: str
    source_path: str


# --- schema rendering helpers (pure, deterministic) -------------------------------


def _ref_name(ref: str) -> str:
    """`#/components/schemas/Bar` -> `Bar`; `#/$defs/ForecastBlock` -> `ForecastBlock`."""
    return ref.rsplit("/", 1)[-1]


def _format_default(value: Any) -> str:
    """Render a JSON default as Python-ish source (None/True/False, quoted str)."""
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True)


def _schema_type_str(node: Mapping[str, Any]) -> str:
    """Render a JSON-schema node as a compact human type string."""
    if "$ref" in node:
        return _ref_name(str(node["$ref"]))
    if "anyOf" in node:
        parts = [_schema_type_str(sub) for sub in node["anyOf"]]
        return " | ".join(parts)
    if "enum" in node:
        vals = ", ".join(_format_default(v) for v in node["enum"])
        return f"enum[{vals}]"
    node_type = node.get("type")
    if node_type == "array":
        items = node.get("items")
        inner = _schema_type_str(items) if isinstance(items, Mapping) else "any"
        return f"array[{inner}]"
    if isinstance(node_type, list):
        return " | ".join(str(t) for t in node_type)
    if node_type is None:
        return "any"
    fmt = node.get("format")
    if node_type == "string" and fmt:
        return f"string ({fmt})"
    return str(node_type)


def _params_from_schema(schema: Mapping[str, Any]) -> tuple[ParamDoc, ...]:
    """Turn a JSON-schema object's `properties` into ParamDocs, in schema order.

    Property order is the definition (signature) order Pydantic emits — stable
    across runs — so we preserve it rather than re-sort. `required` reflects the
    schema's `required` list; `default` reflects the declared default and is
    `None` for a required field.
    """
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    required = set(schema.get("required", []))
    docs: list[ParamDoc] = []
    for name, node in properties.items():
        node_map: Mapping[str, Any] = node if isinstance(node, Mapping) else {}
        is_required = name in required
        default: str | None = None
        if not is_required and "default" in node_map:
            default = _format_default(node_map["default"])
        docs.append(
            ParamDoc(
                name=str(name),
                type_str=_schema_type_str(node_map),
                required=is_required,
                default=default,
            )
        )
    return tuple(docs)


def _first_sentence(text: str) -> str:
    """First sentence of the first paragraph — the one-line summary."""
    para = text.strip().split("\n\n", 1)[0].strip()
    if not para:
        return ""
    marker = para.find(". ")
    if marker != -1:
        return para[: marker + 1].strip()
    if para.endswith("."):
        return para
    return para.split("\n", 1)[0].strip()


def _repo_source_path(obj: object) -> str:
    """Repo-relative source path (`src/...`) for a function or class.

    Derived from the `src` segment down so it carries no host-specific prefix —
    the determinism the CI gate depends on. Falls back to the dotted module name
    when the source file can't be located.
    """
    try:
        src = inspect.getsourcefile(obj)  # type: ignore[arg-type]
    except TypeError:
        src = None
    if src:
        parts = Path(src).parts
        if "src" in parts:
            idx = len(parts) - 1 - list(reversed(parts)).index("src")
            return "/".join(parts[idx:])
    module = inspect.getmodule(obj)
    name = module.__name__ if module is not None else ""
    return "src/" + name.replace(".", "/") + ".py"


def _annotation_name(fn: Callable[..., Any]) -> str:
    """Rendered name of a function's return annotation (the return-shape fallback)."""
    try:
        annotation = inspect.signature(fn).return_annotation
    except (ValueError, TypeError):
        return "—"
    if annotation is inspect.Signature.empty:
        return "—"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("market_analyser.", "")


# --- tool introspection -----------------------------------------------------------


def _tool_return(info: Any) -> tuple[str, tuple[ParamDoc, ...]]:
    """Render a tool's return shape.

    Primary: the tool's output schema (present for every tool under this repo's
    pinned `mcp`). When FastMCP synthesised a generic `<name>DictOutput` wrapper
    (a non-model return) or the schema carries no top-level properties, fall back
    to the function's return annotation and render no field table. When there is
    no output schema at all, the fallback is the sole path.
    """
    output_schema = info.output_schema
    if isinstance(output_schema, Mapping) and output_schema:
        title = str(output_schema.get("title", ""))
        properties = output_schema.get("properties")
        if title.endswith("DictOutput") or not isinstance(properties, Mapping) or not properties:
            return _annotation_name(info.fn), ()
        return title, _params_from_schema(output_schema)
    return _annotation_name(info.fn), ()


def introspect_tools(server: FastMCP) -> tuple[ToolDoc, ...]:
    """Introspect the MCP tool registry into ordered ToolDocs (alphabetical).

    Reads the FastMCP tool manager, whose `Tool` records carry the input schema
    (`parameters`), the `output_schema`, and the bound function (`fn`) — the last
    of which the wire `ListToolsResult` omits but the source link and
    return-annotation fallback need.
    """
    docs: list[ToolDoc] = []
    for info in server._tool_manager.list_tools():
        description = info.description or ""
        parameters = info.parameters if isinstance(info.parameters, Mapping) else {}
        return_shape, return_fields = _tool_return(info)
        module = inspect.getmodule(info.fn)
        docs.append(
            ToolDoc(
                name=info.name,
                summary=_first_sentence(description),
                description=description,
                params=_params_from_schema(parameters),
                return_shape=return_shape,
                return_fields=return_fields,
                source_module=module.__name__ if module is not None else "",
                source_path=_repo_source_path(info.fn),
            )
        )
    return tuple(sorted(docs, key=lambda doc: doc.name))


# --- route introspection ----------------------------------------------------------


def _route_params(operation: Mapping[str, Any]) -> tuple[ParamDoc, ...]:
    docs: list[ParamDoc] = []
    for param in operation.get("parameters", []):
        if not isinstance(param, Mapping):
            continue
        schema = param.get("schema")
        schema_map: Mapping[str, Any] = schema if isinstance(schema, Mapping) else {}
        default: str | None = None
        if "default" in schema_map:
            default = _format_default(schema_map["default"])
        docs.append(
            ParamDoc(
                name=str(param.get("name", "")),
                type_str=_schema_type_str(schema_map),
                required=bool(param.get("required", False)),
                default=default,
            )
        )
    return tuple(docs)


def _json_schema_of(container: Mapping[str, Any]) -> Mapping[str, Any] | None:
    schema = container.get("content", {}).get("application/json", {}).get("schema")
    return schema if isinstance(schema, Mapping) else None


def _request_schema(operation: Mapping[str, Any]) -> str | None:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, Mapping):
        return None
    schema = _json_schema_of(request_body)
    return _schema_type_str(schema) if schema is not None else None


def _response_schema(operation: Mapping[str, Any]) -> str | None:
    responses = operation.get("responses", {})
    for code in ("200", "201"):
        response = responses.get(code)
        if isinstance(response, Mapping):
            schema = _json_schema_of(response)
            if schema is not None:
                return _schema_type_str(schema)
    return None


def introspect_routes(app: FastAPI) -> tuple[RouteDoc, ...]:
    """Introspect the FastAPI OpenAPI document into ordered RouteDocs.

    One record per (path, method) in `app.openapi()`, sorted by path then method.
    The auth-exempt `/healthz` liveness probe is included and flagged as such (the
    `auth` field); every other route is renderer-bearer gated by the central
    middleware. The `/mcp` transport is not a FastAPI route, so it never appears
    here — this surface is REST-only by construction.
    """
    spec = app.openapi()
    paths = spec.get("paths", {})
    docs: list[RouteDoc] = []
    for path, methods in paths.items():
        if not isinstance(methods, Mapping):
            continue
        for method, operation in methods.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, Mapping):
                continue
            auth = "none (liveness probe)" if path == "/healthz" else "renderer bearer"
            docs.append(
                RouteDoc(
                    path=str(path),
                    method=method.upper(),
                    summary=str(operation.get("summary", "")),
                    description=str(operation.get("description", "")),
                    auth=auth,
                    params=_route_params(operation),
                    request_schema=_request_schema(operation),
                    response_schema=_response_schema(operation),
                )
            )
    return tuple(sorted(docs, key=lambda doc: (doc.path, doc.method)))


# --- event introspection ----------------------------------------------------------


def introspect_events() -> tuple[EventDoc, ...]:
    """Introspect the SSE event `TYPE_REGISTRY` into ordered EventDocs (by kind).

    Each registered kind maps to its per-version payload model; the record
    carries the model's version, docstring summary/description, and the payload
    field table derived from its JSON schema.
    """
    docs: list[EventDoc] = []
    for kind, model in TYPE_REGISTRY.items():
        schema = model.model_json_schema()
        version = int(getattr(model, "VERSION"))  # noqa: B009 — ClassVar off abstract type
        doc = inspect.cleandoc(model.__doc__ or "")
        docs.append(
            EventDoc(
                kind=kind,
                version=version,
                summary=_first_sentence(doc),
                description=doc,
                payload_fields=_params_from_schema(schema),
                source_model=model.__name__,
                source_path=_repo_source_path(model),
            )
        )
    return tuple(sorted(docs, key=lambda doc: doc.kind))
