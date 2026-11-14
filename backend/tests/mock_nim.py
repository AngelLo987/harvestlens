"""Local stand-in for the NVIDIA chat completions API, for testing the map agent offline.

Streams reasoning, split tool-call arguments and usage like the real endpoint, including one
deliberately invalid call to exercise the agent's error path. It is NOT Nemotron: always run
it with a NEMOTRON_MODEL that says "mock" so the UI cannot be mistaken for the real model.

    uvicorn tests.mock_nim:app --port 8799
"""
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

SCRIPT = [
    [("highlight_fields", {"field_ids": ["NOPE-0000"]})],
    [("find_fields", {"county_ids": ["marathon-wi"], "harvest_type": "silage", "sort": "confidence_asc", "limit": 5})],
    [("set_map_filters", {"county_ids": ["marathon-wi"], "harvest_type": "silage"}), ("highlight_fields", None)],
    [("open_field_panel", None)],
]


def chunk(delta=None, usage=None):
    body = {"id": "mock", "object": "chat.completion.chunk", "created": 0, "model": "mock",
            "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": None}]}
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    assert body["stream"] and body["tools"] and body["parallel_tool_calls"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    tool_msgs = [m for m in body["messages"] if m["role"] == "tool"]
    assistant_turns = sum(1 for m in body["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
    ids = []
    for m in tool_msgs:
        result = json.loads(m["content"])
        ids = [f["id"] for f in result.get("sample", [])] or ids

    def stream():
        yield chunk({"role": "assistant", "reasoning_content": f"Mock reasoning for step {assistant_turns + 1}. "})
        yield chunk({"reasoning_content": "Deciding the next tool."})
        if assistant_turns < len(SCRIPT):
            for i, (name, args) in enumerate(SCRIPT[assistant_turns]):
                if name == "highlight_fields" and args is None:
                    args = {"field_ids": ids, "label": "Least certain silage"}
                if name == "open_field_panel":
                    args = {"field_id": ids[0]}
                raw = json.dumps(args)
                half = len(raw) // 2
                yield chunk({"tool_calls": [{"index": i, "id": f"call_{assistant_turns}_{i}", "type": "function", "function": {"name": name, "arguments": raw[:half]}}]})
                yield chunk({"tool_calls": [{"index": i, "function": {"arguments": raw[half:]}}]})
        else:
            for word in f"MOCK ANSWER: highlighted {len(ids)} fields, starting with {ids[0]}.".split(" "):
                yield chunk({"content": word + " "})
        yield chunk(usage={"prompt_tokens": 900, "completion_tokens": 60, "total_tokens": 960})
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
