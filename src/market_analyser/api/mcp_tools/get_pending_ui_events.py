"""`get_pending_ui_events` MCP surface (Plan 0014; extracted Plan 0017).

This module owns three responsibilities that share the UI-event buffer dep and
were always one logical unit (ADR-0021):

- the `get_pending_ui_events(since=None, drain=True)` tool — the reliable,
  draining contract the agent calls to consume the user's chart gestures;
- the `ui-events://recent` resource — a non-draining (peek) re-readable view;
- the `on_append` callback that fires `notifications/resources/updated` for the
  resource on every buffer append (best-effort: stateless_http has no persistent
  session between requests, so it no-ops gracefully when none is active).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl

from market_analyser.ui_events import UIEventEnvelope
from market_analyser.ui_events.buffer import UIEventBuffer

logger = logging.getLogger(__name__)

UI_EVENTS_RESOURCE_URI = "ui-events://recent"

GET_PENDING_UI_EVENTS_DESCRIPTION = (
    "Read recent UI events the user generated in the chart viewer — drag-selected "
    "ranges, single bar clicks, and agent-mode toggles. Events are buffered ONLY "
    "while **agent mode** is ON; when it is OFF this returns an empty list. By "
    "default (drain=True) each call drains the events it returns, so consecutive "
    "draining reads return disjoint sets — call it when you are ready to act on the "
    "user's gestures. Pass drain=False to peek without consuming. `since` returns "
    f"only events stamped strictly after that timestamp. The same buffer is also "
    f"exposed (non-draining) as the MCP resource {UI_EVENTS_RESOURCE_URI}, which "
    "you can subscribe to for update notifications; dedupe across the tool and the "
    "resource on each event's `event_id`."
)

UI_EVENTS_RESOURCE_DESCRIPTION = (
    "Most recent UI events from the chart viewer (range selections, bar clicks, "
    "agent-mode toggles), newest last. Reading this resource does NOT drain the "
    "buffer — use the get_pending_ui_events tool with drain=True to consume. "
    "Populated only while agent mode is ON; empty otherwise. A "
    "notifications/resources/updated notification fires on every new event."
)


def _make_resource_update_sender(server: FastMCP) -> Callable[[str], None]:
    """Build the best-effort `notifications/resources/updated` sender bound to
    `server`. Returns a sync callable so it can run as a buffer `on_append`
    callback.

    Best-effort by design (ADR-0021): the transport is `stateless_http=True`, so
    between MCP requests there is no persistent session to push to. When no active
    MCP request context (and thus no session/loop) is available — the usual case
    for an append driven by the renderer's `POST /ui_events` — this logs at DEBUG
    and returns without raising. The `get_pending_ui_events` tool is the reliable
    contract; this notification is the opportunistic low-latency nudge for clients
    that surface resource updates to the model.
    """
    # Hold strong references to in-flight notification tasks so the event loop
    # doesn't GC them mid-send (RUF006); each removes itself on completion.
    pending: set[asyncio.Task[None]] = set()

    def _send(uri: str) -> None:
        try:
            session = server.get_context().session
            loop = asyncio.get_running_loop()
        except Exception:
            logger.debug("no active MCP session/loop for resource-updated (%s); skipping", uri)
            return
        task = loop.create_task(session.send_resource_updated(AnyUrl(uri)))
        pending.add(task)
        task.add_done_callback(pending.discard)

    return _send


class _ResourceUpdateNotifier:
    """Buffer `on_append` callback that fires a resource-updated notification for
    the UI-events resource on every append. `send` is injected so tests can spy
    on the per-append invocation; production passes the best-effort sender."""

    def __init__(self, send: Callable[[str], None], uri: str) -> None:
        self._send = send
        self._uri = uri

    def __call__(self, envelope: UIEventEnvelope) -> None:
        self._send(self._uri)


def register_get_pending_ui_events(server: FastMCP, *, ui_event_buffer: UIEventBuffer) -> None:
    """Bind the `get_pending_ui_events` tool, the `ui-events://recent` resource, and
    the per-append resource-update notifier to `server`. The buffer is captured by
    closure; all three responsibilities share it and move together as one unit."""

    @server.tool(description=GET_PENDING_UI_EVENTS_DESCRIPTION)
    def get_pending_ui_events(
        since: datetime | None = None,
        drain: bool = True,
    ) -> list[UIEventEnvelope]:
        if drain:
            return ui_event_buffer.drain(since=since)
        return ui_event_buffer.peek(since=since)

    @server.resource(UI_EVENTS_RESOURCE_URI, description=UI_EVENTS_RESOURCE_DESCRIPTION)
    def read_recent_ui_events() -> str:
        # Non-draining (peek): the resource is a re-readable view; the tool with
        # drain=True is the consuming path. FastMCP serialises the str return as
        # the resource's text content.
        return json.dumps([e.model_dump(mode="json") for e in ui_event_buffer.peek()])

    # Fire notifications/resources/updated on every buffer append. Best-effort:
    # in stateless_http mode there is no persistent session between MCP requests,
    # so the sender no-ops gracefully when none is active (ADR-0021's open
    # question — the get_pending_ui_events tool is the reliable contract).
    ui_event_buffer.on_append(
        _ResourceUpdateNotifier(_make_resource_update_sender(server), UI_EVENTS_RESOURCE_URI),
    )


__all__ = [
    "GET_PENDING_UI_EVENTS_DESCRIPTION",
    "UI_EVENTS_RESOURCE_DESCRIPTION",
    "UI_EVENTS_RESOURCE_URI",
    "_ResourceUpdateNotifier",
    "_make_resource_update_sender",
    "register_get_pending_ui_events",
]
