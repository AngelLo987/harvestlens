"""Agentic copilot: natural language -> structured filters -> grounded answer.

Uses NVIDIA Nemotron (OpenAI-compatible NIM endpoint) when NVIDIA_API_KEY is set,
otherwise a deterministic rule-based parser so the demo never depends on network access.
The LLM only ever produces a JSON plan and a narrative over numbers HarvestLens computed;
it never invents field data.
"""
from __future__ import annotations

import json
import os
import re

import httpx

NIM_BASE = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_URL = NIM_BASE + "/chat/completions"
NIM_MODEL = os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")

STATE_NAMES = {"CA": "california", "WI": "wisconsin", "IA": "iowa", "PA": "pennsylvania"}

INTENTS = ["filter", "compare", "summary", "explain", "export", "anomalies"]

PLAN_PROMPT = """You are the query planner for HarvestLens, a USDA tool that classifies corn fields as silage or grain.
Convert the analyst's question into a JSON plan. Respond with JSON only, no prose.
Schema:
{{"intent": one of {intents},
  "counties": list of county ids from {counties} (empty = all),
  "harvest_type": "silage" | "grain" | null,
  "min_confidence": number 0-1 or null,
  "max_confidence": number 0-1 or null,
  "anomalies_only": boolean,
  "field_id": string or null,
  "export_format": "csv" | "geojson" | null,
  "clarification": string or null  // set ONLY if the question cannot be answered with this data
}}
"high-confidence" means min_confidence 0.9. "uncertain"/"ambiguous"/"anomalies" means anomalies_only true.
Field ids look like YOL-0012."""

ANSWER_PROMPT = """You are HarvestLens, an analyst copilot for USDA. Using ONLY the JSON facts given, answer the
analyst's question in 2-4 concise sentences. Quote numbers exactly. Mention data caveats (low confidence,
cloud gaps) when they appear in the facts. No markdown headers."""


def _nim(messages: list[dict], max_tokens: int = 400) -> str | None:
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        return None
    try:
        r = httpx.post(NIM_URL, timeout=30, headers={"Authorization": f"Bearer {key}"}, json={
            "model": NIM_MODEL, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},  # short structured replies; the map agent is the reasoning path
        })
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    except Exception:
        return None


def llm_available() -> bool:
    return bool(os.environ.get("NVIDIA_API_KEY"))


def rule_plan(q: str, counties: list[dict]) -> dict:
    ql = q.lower()
    plan = {"intent": "filter", "counties": [], "harvest_type": None, "min_confidence": None,
            "max_confidence": None, "anomalies_only": False, "field_id": None,
            "export_format": None, "clarification": None}
    for c in counties:
        base = c["name"].lower().replace(" county", "")
        state_name = STATE_NAMES.get(c["state"], "")
        if (re.search(rf"\b{re.escape(base)}\b", ql) or re.search(rf"\b{c['state']}\b", q)
                or (state_name and state_name in ql)):
            plan["counties"].append(c["id"])
    if re.search(r"\bsilage\b", ql) and not re.search(r"\bgrain\b", ql):
        plan["harvest_type"] = "silage"
    elif re.search(r"\bgrain\b", ql) and not re.search(r"\bsilage\b", ql):
        plan["harvest_type"] = "grain"
    m = re.search(r"(\d{2,3})\s*%", ql)
    if re.search(r"high[- ]confidence|\bconfident\b|\bcertain\b", ql):
        plan["min_confidence"] = 0.9
    if m and re.search(r"(above|over|more than|at least|>)\s*\d{2,3}\s*%", ql):
        plan["min_confidence"] = int(m.group(1)) / 100
    elif m and re.search(r"(below|under|less than|<)\s*\d{2,3}\s*%", ql):
        plan["max_confidence"] = int(m.group(1)) / 100
    if re.search(r"low[- ]confidence|uncertain|ambiguous|anomal|suspicious|flag|conflict", ql):
        plan["anomalies_only"] = True
        plan["intent"] = "anomalies"
    fid = re.search(r"\b([a-z]{3}-\d{4})\b", ql)
    if fid:
        plan["field_id"] = fid.group(1).upper()
        plan["intent"] = "explain"
    elif re.search(r"\bcompare|versus|\bvs\.?\b|across", ql):
        plan["intent"] = "compare"
    elif re.search(r"summar|overview|distribution|how many|breakdown|share|percent", ql):
        plan["intent"] = "summary"
    if re.search(r"export|download|csv|geojson", ql):
        plan["intent"] = "export"
        plan["export_format"] = "geojson" if "geojson" in ql else "csv"
    understood = (plan["counties"] or plan["harvest_type"] or plan["field_id"] or plan["anomalies_only"]
                  or plan["intent"] != "filter" or plan["min_confidence"] is not None)
    if not understood:
        plan["clarification"] = ("I can filter fields by county, harvest type (silage/grain) and confidence, "
                                 "compare counties, explain a field (e.g. YOL-0012) or export results. "
                                 "Try: “Show high-confidence silage fields in Marathon County”.")
    return plan


def plan_query(q: str, counties: list[dict]) -> tuple[dict, str]:
    ids = [c["id"] for c in counties]
    sys = PLAN_PROMPT.format(intents=INTENTS, counties=ids) + "\nCounties: " + json.dumps(
        [{"id": c["id"], "name": c["name"], "state": c["state"]} for c in counties])
    out = _nim([{"role": "system", "content": sys}, {"role": "user", "content": q}], 300)
    if out:
        m = re.search(r"\{.*\}", out, re.S)
        try:
            plan = json.loads(m.group(0)) if m else None
            if plan and plan.get("intent") in INTENTS:
                base = rule_plan(q, counties)
                base.update({k: v for k, v in plan.items() if k in base})
                base["counties"] = [c for c in base.get("counties") or [] if c in ids]
                return base, "nemotron"
        except (json.JSONDecodeError, AttributeError):
            pass
    return rule_plan(q, counties), "rules"


def narrate(q: str, facts: dict, fallback: str) -> tuple[str, str]:
    out = _nim([{"role": "system", "content": ANSWER_PROMPT},
                {"role": "user", "content": f"Question: {q}\nFacts: {json.dumps(facts, default=str)}"}], 300)
    return (out, "nemotron") if out else (fallback, "rules")
