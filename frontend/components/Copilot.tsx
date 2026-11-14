"use client";

import { useEffect, useRef, useState } from "react";

export interface CopilotResponse {
  answer: string;
  planner: string;
  narrator: string;
  latency_ms: number;
  needs_clarification?: boolean;
  plan: Record<string, unknown>;
  action: null | { type: "filter" | "compare" | "export" | "select_field"; filters?: Record<string, unknown>; field_id?: string; export_format?: string | null };
}

interface Msg { role: "user" | "assistant"; text: string; meta?: string }

export const SAMPLE_QUERIES = [
  "Show all high-confidence silage fields in Yolo County",
  "Compare silage vs grain across all counties",
  "Which fields in Marathon County are uncertain?",
  "Explain YOL-0012",
  "Export grain fields in Story County as CSV",
];

export default function Copilot({ onResult, external }: { onResult: (r: CopilotResponse) => void; external: { q: string; n: number } | null }) {
  const [msgs, setMsgs] = useState<Msg[]>([{
    role: "assistant",
    text: "Ask about silage and grain fields by county, by confidence, or by field ID.",
  }]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  useEffect(() => { if (external) ask(external.q); }, [external?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  async function ask(text: string) {
    const query = text.trim();
    if (!query || busy) return;
    setMsgs((m) => [...m, { role: "user", text: query }]);
    setQ("");
    setBusy(true);
    try {
      const r = await fetch("/api/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query }) });
      const data = (await r.json()) as CopilotResponse;
      const via = data.planner === "nemotron" || data.narrator === "nemotron" ? "Nemotron" : "rule engine";
      setMsgs((m) => [...m, { role: "assistant", text: data.answer, meta: `${data.plan.intent} · via ${via} · ${Math.round(data.latency_ms)} ms` }]);
      onResult(data);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `Sorry, the backend is unreachable (${String(e)}).` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
        {msgs.map((m, i) => (
          <div key={i}>
            <div className="eyebrow text-muted">{m.role === "user" ? "You" : "HarvestLens"}</div>
            <p className={`mt-1 text-[14px] leading-[22px] ${m.role === "user" ? "font-medium text-ink" : "text-body"}`}>{m.text}</p>
            {m.meta && <div className="eyebrow mt-1.5 text-[10px] text-muted">{m.meta}</div>}
          </div>
        ))}
        {busy && <div className="eyebrow text-muted">Reading fields…</div>}
        {msgs.length <= 1 && (
          <div>
            {SAMPLE_QUERIES.map((s) => (
              <button key={s} onClick={() => ask(s)} className="flex w-full items-center justify-between gap-3 border-t border-line py-2.5 text-left text-[14px] hover:text-muted">{s}<span aria-hidden="true">→</span></button>
            ))}
          </div>
        )}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="p-3">
        <div className="flex items-center gap-2 rounded-full border border-ink py-1 pl-4 pr-1">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about any county or field"
            aria-label="Ask the copilot" className="min-w-0 flex-1 bg-transparent text-[14px] outline-none" />
          <button disabled={busy || !q.trim()} className="pill pill-solid disabled:opacity-30">Ask</button>
        </div>
        <p className="mt-2 px-1 text-[11px] leading-4 text-muted" title="Supported: county names or states, silage/grain, 'high-confidence', 'over 90%', 'uncertain', 'compare', field IDs like MAR-0042, 'export as geojson'">
          Try a county, silage or grain, “over 90%”, “uncertain”, “compare”, or a field ID.
        </p>
      </form>
    </div>
  );
}
