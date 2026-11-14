"""Nemotron map agent: a streaming tool-calling loop that operates the HarvestLens map.

The model never sees or invents field data except through tool results, and it can only
change the map through the validated tools below. Every step is streamed to the UI as an
event (reasoning, tool call, tool result, map action, token usage) so a run can be audited.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter
from typing import Callable, Iterator

import openai
from openai import OpenAI

from . import copilot

MAX_STEPS = 8
MAX_HIGHLIGHT = 200
FLAGS = ["low_confidence", "model_disagreement", "cloud_gaps", "conflicting_signals", "optical_sar_timing_mismatch"]

SYSTEM_PROMPT = """You are the HarvestLens map agent, used by USDA analysts to classify corn fields as silage or grain.
You operate a live map through tools. Work in this order: query the data, act on the map, then answer.

Rules:
- Every number, field ID and explanation in your answer must come from a tool result in the CURRENT turn. Never estimate or invent one.
- Earlier answers in this conversation are not evidence. For any question about a specific field, call get_field now, even if you discussed that field before.
- Explain a field only with what its evidence, flags and features actually say. Do not add mechanisms or details the tool result does not contain.
- When the user asks to see, show, find or go to something, change the map. Do not only describe it.
- Use highlight_fields for specific fields you are calling attention to, and set_map_filters for whole categories.
- Finish with 1-3 plain sentences saying what you found and what you changed on the map. Plain text only, no markdown.
- If the data cannot answer the question, say so instead of guessing.
- All field data in this deployment is simulated. Say so only if the user asks about real-world accuracy or where the data comes from; the interface already discloses it.

Counties: {counties}
Field IDs look like YOL-0012. Confidence is 0-1.
Review flags mean exactly this:
- low_confidence: confidence below 0.75
- model_disagreement: the 90% interval of P(silage) across the ensemble is wider than 0.3
- cloud_gaps: under 40% of optical observations from July onward were cloud-free
- conflicting_signals: most evidence signals point away from the assigned label
- optical_sar_timing_mismatch: optical and radar harvest dates differ by more than 21 days"""


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}}


_COUNTIES = {"type": "array", "items": {"type": "string"}, "description": "County ids. Empty or omitted means all counties."}
_TYPE = {"type": "string", "enum": ["silage", "grain"]}
_CONF = {"type": "number", "minimum": 0, "maximum": 1}
_FILTERS = {"county_ids": _COUNTIES, "harvest_type": _TYPE, "min_confidence": _CONF, "max_confidence": _CONF,
            "flagged_only": {"type": "boolean", "description": "Only fields with at least one review flag."}}

TOOLS = [
    _fn("find_fields", "Search classified fields. Returns aggregate statistics and a sorted sample. Does not change the map.", {
        **_FILTERS, "flag": {"type": "string", "enum": FLAGS, "description": "Only fields with this review flag."},
        "sort": {"type": "string", "enum": ["confidence_asc", "confidence_desc", "area_desc"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    _fn("compare_counties", "Per-county statistics: field counts, silage share of area, mean confidence, flagged fields. Does not change the map.", {"county_ids": _COUNTIES}),
    _fn("get_field", "Full record for one field: label, probability interval, evidence, flags and features.", {"field_id": {"type": "string"}}, ["field_id"]),
    _fn("set_map_filters", "Replace the map's filters and zoom to the result.", _FILTERS),
    _fn("fly_to", "Move the camera to counties or one field. Give county_ids or field_id.", {"county_ids": _COUNTIES, "field_id": {"type": "string"}}),
    _fn("highlight_fields", "Outline specific fields in the agent color, whatever the filters are, and zoom to them.", {
        "field_ids": {"type": "array", "items": {"type": "string"}}, "label": {"type": "string", "description": "Short description of why they are highlighted."}}, ["field_ids"]),
    _fn("open_field_panel", "Open the detail panel with one field's time series and evidence.", {"field_id": {"type": "string"}}, ["field_id"]),
    _fn("clear_map", "Remove all filters, highlights and the open field panel.", {}),
]


class ToolError(Exception):
    """An invalid tool call. The message goes back to the model so it can correct itself."""


def _bbox(polygons: list[list[list[float]]]) -> list[float] | None:
    pts = [p for ring in polygons for p in ring]
    if not pts:
        return None
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def _compact(r: dict) -> dict:
    return {k: r[k] for k in ("id", "county_id", "label", "confidence", "interval", "area_ha", "harvest_date", "flags")}


class MapTools:
    """Executes tool calls against the classified fields. Returns (result, map_actions)."""

    def __init__(self, fields: dict, counties: list[dict], select: Callable, summarize: Callable):
        self.fields, self.counties, self.select, self.summarize = fields, counties, select, summarize
        self.names = {
            "find_fields": self.find_fields, "compare_counties": self.compare_counties, "get_field": self.get_field,
            "set_map_filters": self.set_map_filters, "fly_to": self.fly_to, "highlight_fields": self.highlight_fields,
            "open_field_panel": self.open_field_panel, "clear_map": self.clear_map,
        }

    def run(self, name: str, args: dict) -> tuple[dict, list[dict]]:
        if name not in self.names:
            raise ToolError(f"Unknown tool {name!r}. Available: {sorted(self.names)}")
        try:
            return self.names[name](**args)
        except TypeError as e:
            raise ToolError(f"Bad arguments for {name}: {e}") from e

    def _county_ids(self, ids: list[str] | None) -> list[str]:
        valid = {c["id"] for c in self.counties}
        bad = [c for c in ids or [] if c not in valid]
        if bad:
            raise ToolError(f"Unknown county ids {bad}. Valid ids: {sorted(valid)}")
        return list(ids or [])

    def _field(self, field_id: str) -> dict:
        r = self.fields.get(str(field_id).upper())
        if not r:
            raise ToolError(f"No field with id {field_id!r}. Use find_fields to get valid ids.")
        return r

    @staticmethod
    def _check(harvest_type, min_confidence, max_confidence):
        if harvest_type not in (None, "silage", "grain"):
            raise ToolError("harvest_type must be 'silage' or 'grain'")
        for v in (min_confidence, max_confidence):
            if v is not None and not 0 <= v <= 1:
                raise ToolError("Confidence values must be between 0 and 1")

    def find_fields(self, county_ids=None, harvest_type=None, min_confidence=None, max_confidence=None,
                    flagged_only=False, flag=None, sort="confidence_asc", limit=15):
        self._check(harvest_type, min_confidence, max_confidence)
        if flag is not None and flag not in FLAGS:
            raise ToolError(f"flag must be one of {FLAGS}")
        recs = self.select(self._county_ids(county_ids) or None, harvest_type, min_confidence, max_confidence, flagged_only)
        if flag:
            recs = [r for r in recs if flag in r["flags"]]
        keys = {"confidence_asc": lambda r: r["confidence"], "confidence_desc": lambda r: -r["confidence"], "area_desc": lambda r: -r["area_ha"]}
        if sort not in keys:
            raise ToolError(f"sort must be one of {sorted(keys)}")
        s = self.summarize(recs)
        top = sorted(recs, key=keys[sort])[: max(1, min(int(limit), 50))]
        return {"matched": len(recs), "silage_fields": s["silage_fields"], "grain_fields": s["grain_fields"], "area_ha": s["area_ha"],
                "mean_confidence": s["mean_confidence"], "flagged": s["flagged"],
                "flag_counts": dict(Counter(f for r in recs for f in r["flags"])), "sample": [_compact(r) for r in top]}, []

    def compare_counties(self, county_ids=None):
        ids = self._county_ids(county_ids)
        out = {}
        for c in self.counties:
            if not ids or c["id"] in ids:
                s = self.summarize(self.select([c["id"]]))
                out[c["id"]] = {"name": c["name"], "state": c["state"], **{k: s[k] for k in (
                    "fields", "silage_fields", "grain_fields", "area_ha", "silage_share_area", "mean_confidence", "flagged")}}
        return {"counties": out}, []

    def get_field(self, field_id):
        r = self._field(field_id)
        return {**_compact(r), "prob_silage": r["prob_silage"], "evidence": r["evidence"], "features": r["features"], "source": r["source"]}, []

    def set_map_filters(self, county_ids=None, harvest_type=None, min_confidence=None, max_confidence=None, flagged_only=False):
        self._check(harvest_type, min_confidence, max_confidence)
        ids = self._county_ids(county_ids)
        recs = self.select(ids or None, harvest_type, min_confidence, max_confidence, flagged_only)
        action = {"type": "filters", "bbox": _bbox([r["polygon"] for r in recs]), "filters": {
            "county": ids, "type": harvest_type, "min_conf": min_confidence, "max_conf": max_confidence, "anomalies": bool(flagged_only)}}
        return {"ok": True, "fields_visible": len(recs)}, [action]

    def fly_to(self, county_ids=None, field_id=None):
        if field_id:
            recs = [self._field(field_id)]
        else:
            ids = self._county_ids(county_ids)
            if not ids:
                raise ToolError("Give county_ids or field_id")
            recs = self.select(ids)
        return {"ok": True}, [{"type": "fly", "bbox": _bbox([r["polygon"] for r in recs])}]

    def highlight_fields(self, field_ids, label=""):
        if not field_ids:
            raise ToolError("field_ids is empty")
        recs = [self._field(f) for f in field_ids[:MAX_HIGHLIGHT]]
        geo = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [r["polygon"]]}, "properties": {"id": r["id"]}} for r in recs]}
        return {"ok": True, "highlighted": len(recs)}, [{"type": "highlight", "label": str(label)[:80], "geojson": geo, "bbox": _bbox([r["polygon"] for r in recs])}]

    def open_field_panel(self, field_id):
        r = self._field(field_id)
        return {"ok": True, "opened": r["id"]}, [{"type": "open_field", "field_id": r["id"]}]

    def clear_map(self):
        return {"ok": True}, [{"type": "clear"}]


def run(query: str, history: list[dict], map_state: dict, tools: MapTools) -> Iterator[dict]:
    """Run one agent turn, yielding UI events until the model stops calling tools."""
    model, started = copilot.NIM_MODEL, time.perf_counter()
    yield {"event": "start", "run_id": uuid.uuid4().hex[:8], "model": model}
    client = OpenAI(base_url=copilot.NIM_BASE, api_key=os.environ["NVIDIA_API_KEY"], timeout=120, max_retries=1)
    system = SYSTEM_PROMPT.format(counties=json.dumps([{k: c[k] for k in ("id", "name", "state")} for c in tools.counties]))
    messages: list[dict] = [{"role": "system", "content": system}, *history,
                            {"role": "user", "content": f"{query}\n\n[Current map state: {json.dumps(map_state)}]"}]
    prompt_tokens = completion_tokens = tool_count = 0
    ttft_first = None

    try:
        for step in range(1, MAX_STEPS + 1):
            yield {"event": "step", "step": step}
            t0, ttft = time.perf_counter(), None
            content, calls, usage = [], {}, None
            with client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, tool_choice="none" if step == MAX_STEPS else "auto",
                parallel_tool_calls=True, temperature=1, top_p=0.95, max_tokens=16384, seed=42, stream=True,
                stream_options={"include_usage": True}, extra_body={"chat_template_kwargs": {"enable_thinking": True}},
            ) as stream:
                for chunk in stream:
                    if chunk.usage:
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if reasoning:
                        yield {"event": "reasoning", "step": step, "delta": reasoning}
                    if delta.content:
                        content.append(delta.content)
                        yield {"event": "answer", "delta": delta.content}
                    for tc in delta.tool_calls or []:
                        c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        c["id"] = tc.id or c["id"]
                        if tc.function:
                            c["name"] = tc.function.name or c["name"]
                            c["args"] += tc.function.arguments or ""

            elapsed = (time.perf_counter() - t0) * 1000
            ttft_first = ttft if ttft_first is None else ttft_first
            if usage:
                prompt_tokens += usage.prompt_tokens or 0
                completion_tokens += usage.completion_tokens or 0
            yield {"event": "usage", "step": step, "ttft_ms": round(ttft or 0), "ms": round(elapsed),
                   "prompt_tokens": usage.prompt_tokens if usage else None, "completion_tokens": usage.completion_tokens if usage else None}

            if not calls:
                break
            ordered = [calls[i] for i in sorted(calls)]
            messages.append({"role": "assistant", "content": "".join(content) or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in ordered]})
            for c in ordered:
                tool_count += 1
                t1 = time.perf_counter()
                try:
                    args = json.loads(c["args"] or "{}")
                except json.JSONDecodeError:
                    args = None
                yield {"event": "tool_call", "id": c["id"], "step": step, "name": c["name"], "args": args if args is not None else c["args"]}
                actions: list[dict] = []
                try:
                    if not isinstance(args, dict):
                        raise ToolError("Arguments were not a valid JSON object")
                    result, actions = tools.run(c["name"], args)
                    ok = True
                except ToolError as e:
                    result, ok = {"error": str(e)}, False
                yield {"event": "tool_result", "id": c["id"], "ok": ok, "ms": round((time.perf_counter() - t1) * 1000, 1), "result": result}
                for a in actions:
                    yield {"event": "map_action", "action": a}
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
    except openai.APIStatusError as e:
        yield {"event": "error", "message": f"NVIDIA API returned HTTP {e.status_code}. Check the key, model access and rate limits."}
        return
    except openai.APIConnectionError:
        yield {"event": "error", "message": "Could not reach the NVIDIA API, or the request timed out."}
        return

    total = time.perf_counter() - started
    yield {"event": "done", "steps": step, "tool_calls": tool_count, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
           "ttft_ms": round(ttft_first or 0), "total_ms": round(total * 1000)}
