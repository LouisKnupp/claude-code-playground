"""Multi-turn agentic loop.

The loop sends the conversation to the LLM with tool definitions, executes any
tool calls the LLM requests, feeds results back, and repeats until the LLM
returns finish_reason='stop' (final answer with no more tool calls).

The full message thread and all tool calls are recorded for audit logging.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from playground.core.audit import AuditLogger
from playground.core.models import AgentResponse, AuditEntry, ToolCallEntry, ToolSearchResult
from playground.core.session import ConversationSession
from playground.providers.base import LLMProvider
from playground.tools import registry as tool_registry
from playground.core.exceptions import ProviderError

_SYSTEM_PROMPT = """\
You are a work context assistant. You have access to tools that search through the user's \
Zoom meeting transcripts and Apple Notes. When answering questions:

1. First locate the relevant documents using search/browse tools:
   - Use search_zoom to find meetings by keyword.
   - Use list_meetings to browse meetings by participant or date (no keyword needed).
   - Use lookup_person to find all meetings where a specific person appears.
   - Use search_notes to search Apple Notes.

2. Once you have identified the right document(s), call get_document with the document_id \
to retrieve the full transcript. Do not answer based on excerpts alone — always fetch the \
full transcript before synthesizing an answer.

3. Always ground your answer in what you actually found. Do not make up information.

4. After your answer, list the sources you used with their deep links so the user can verify.

5. If you find nothing relevant, say so clearly.
"""


def _extract_sources(messages: list[dict]) -> list[ToolSearchResult]:
    """Pull ToolSearchResult objects from get_document tool results in the thread.

    Only documents that were actually fetched in full (via get_document) are
    treated as sources.  Search/browse/lookup results are just query intermediates
    and should not be listed as citations.
    """
    sources: list[ToolSearchResult] = []
    seen_docs: set[str] = set()

    # Walk parallel assistant + tool message pairs to match tool_call_id → tool_name
    tool_call_names: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tc_id = tc.get("id", "")
                tc_name = tc.get("function", {}).get("name", "")
                if tc_id:
                    tool_call_names[tc_id] = tc_name

    for msg in messages:
        if msg.get("role") != "tool":
            continue
        # Only collect from get_document calls
        tc_id = msg.get("tool_call_id", "")
        if tool_call_names.get(tc_id) != "get_document":
            continue
        try:
            payload = json.loads(msg.get("content", "{}"))
        except (ValueError, json.JSONDecodeError):
            continue

        if not payload.get("found"):
            continue

        doc_id = payload.get("document_id", "")
        if doc_id and doc_id not in seen_docs:
            seen_docs.add(doc_id)
            meta = payload.get("metadata", {})
            sources.append(
                ToolSearchResult(
                    document_id=doc_id,
                    source_type=payload.get("source_type", ""),
                    title=payload.get("title", ""),
                    excerpt="",
                    deep_link=payload.get("deep_link", ""),
                    score=1.0,
                    metadata=meta,
                )
            )

    return sources


def run(
    user_query: str,
    session: ConversationSession,
    provider: LLMProvider,
    audit_logger: AuditLogger,
    max_iterations: int = 10,
) -> AgentResponse:
    """Execute one user turn through the agentic loop."""
    start_time = time.monotonic()
    turn_index = session.next_turn_index()
    all_tool_calls: list[ToolCallEntry] = []
    errors: list[str] = []

    # Build the message thread: system + history + new user message
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(session.get_context_messages())
    messages.append({"role": "user", "content": user_query})

    tools = tool_registry.get_all_openai_specs()
    final_content = ""

    for _iteration in range(max_iterations):
        try:
            response = provider.complete_with_tools(messages, tools)
        except ProviderError as exc:
            errors.append(str(exc))
            final_content = f"I encountered an error: {exc}"
            break

        # Build the assistant message for the thread
        assistant_msg: dict = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ]
        messages.append(assistant_msg)

        if response.finish_reason == "stop" or not response.tool_calls:
            final_content = response.content
            break

        # Execute each tool call
        for tc in response.tool_calls:
            tc_start = time.monotonic()
            tc_error: str | None = None
            result: dict = {}

            try:
                result = tool_registry.execute(tc.name, tc.arguments)
            except Exception as exc:
                tc_error = str(exc)
                result = {"error": tc_error, "results": []}
                errors.append(f"Tool {tc.name} failed: {tc_error}")

            tc_latency = int((time.monotonic() - tc_start) * 1000)
            all_tool_calls.append(
                ToolCallEntry(
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    tool_result=result,
                    latency_ms=tc_latency,
                    error=tc_error,
                )
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                }
            )
    else:
        # Hit max iterations without a stop
        errors.append(f"Agent reached max_iterations ({max_iterations}) without completing.")
        if not final_content:
            final_content = "I wasn't able to complete the query within the allowed number of steps."

    latency_ms = int((time.monotonic() - start_time) * 1000)
    sources = _extract_sources(messages)

    # Persist conversation messages
    session.add_user_message(user_query, turn_index)
    session.add_assistant_message(final_content, turn_index)

    # Write audit entry
    audit_entry = AuditEntry(
        id=str(uuid.uuid4()),
        session_id=session.session_id,
        turn_index=turn_index,
        user_query=user_query,
        final_response=final_content,
        tool_calls=all_tool_calls,
        full_message_thread=messages,
        errors=errors,
        latency_ms=latency_ms,
        model_id=provider.model_id,
        created_at=datetime.utcnow(),
    )
    audit_logger.log(audit_entry)

    return AgentResponse(
        content=final_content,
        sources=sources,
        tool_calls=all_tool_calls,
    )
