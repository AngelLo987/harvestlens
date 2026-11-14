"use client";

import { useEffect, useRef, useState } from "react";
import type { Filters } from "@/lib/api";

export type MapAction =
  | { type: "filters"; filters: Filters; bbox: [number, number, number, number] | null }
  | { type: "fly"; bbox: [number, number, number, number] | null }
  | { type: "highlight"; label: string; geojson: GeoJSON.FeatureCollection; bbox: [number, number, number, number] | null }
  | { type: "open_field"; field_id: string }
  | { type: "clear" };

type Item =
  | { kind: "reasoning"; step: number; text: string }
  | { kind: "tool"; id: string; step: number; name: string; args: unknown; ok?: boolean; ms?: number; result?: unknown };

interface Stats { steps: number; tool_calls: number; prompt_tokens: number; completion_tokens: number; ttft_ms: number; total_ms: number }
interface Turn { q: string; items: Item[]; answer: string; stats?: Stats; error?: string; running: boolean; runId?: string }

const SAMPLES = [
  "Find the 5 least certain silage fields in Marathon County, highlight them, and open the worst one",
  "Which county has the most fields needing review? Take me there and show only those",
  "Compare Yolo and Lancaster, then show high-confidence grain in whichever has more silage",
];

function Trace({ item }: { item: Item }) {
  const [open, setOpen] = useState(false);
  if (item.kind === "reasoning") {
    return (
      <details className="font-mono text-[11px] leading-[17px] text-white/55">
        <summary className="cursor-pointer text-white/70">step {item.step} reasoning · {item.text.length} chars</summary>
        <p className="mt-1 whitespace-pre-wrap border-l border-white/15 pl-2">{item.text}</p>
      </details>
    );
  }
  const status = item.ok === undefined ? "running" : item.ok ? `${item.ms} ms` : "rejected";
  return (
    <div className="font-mono text-[11px] leading-[17px]">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-baseline justify-between gap-2 text-left">
        <span className="min-w-0 truncate"><span className={item.ok === false ? "text-[#ff9a7a]" : "text-[#a6e22e]"}>{item.name}</span><span className="text-white/50">({JSON.stringify(item.args)})</span></span>
        <span className="shrink-0 text-white/50">{status}</span>
      </button>
      {open && item.result !== undefined && <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap border-l border-white/15 pl-2 text-white/60">{JSON.stringify(item.result, null, 1)}</pre>}
    </div>
  );
}

export default function MapAgent({ enabled, model, mapState, onAction }: {
  enabled: boolean; model: string; mapState: Record<string, unknown>; onAction: (a: MapAction) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(true);
  const end = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const running = turns.at(-1)?.running ?? false;

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns]);

  const patch = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  function handle(e: Record<string, any>) { // eslint-disable-line @typescript-eslint/no-explicit-any
    switch (e.event) {
      case "start": patch((t) => ({ ...t, runId: e.run_id })); break;
      case "step": patch((t) => ({ ...t, answer: "" })); break; // text before a tool call is narration; only the last step is the answer
      case "reasoning": patch((t) => {
        const last = t.items.at(-1);
        if (last?.kind === "reasoning" && last.step === e.step) return { ...t, items: [...t.items.slice(0, -1), { ...last, text: last.text + e.delta }] };
        return { ...t, items: [...t.items, { kind: "reasoning", step: e.step, text: e.delta }] };
      }); break;
      case "answer": patch((t) => ({ ...t, answer: t.answer + e.delta })); break;
      case "tool_call": patch((t) => ({ ...t, items: [...t.items, { kind: "tool", id: e.id, step: e.step, name: e.name, args: e.args }] })); break;
      case "tool_result": patch((t) => ({ ...t, items: t.items.map((i) => (i.kind === "tool" && i.id === e.id ? { ...i, ok: e.ok, ms: e.ms, result: e.result } : i)) })); break;
      case "map_action": onAction(e.action); break;
      case "done": patch((t) => ({ ...t, stats: e as unknown as Stats, running: false })); break;
      case "error": patch((t) => ({ ...t, error: e.message, running: false })); break;
    }
  }

  async function ask(text: string) {
    const query = text.trim();
    if (!query || running || !enabled) return;
    const history = turns.filter((t) => t.answer).slice(-6).flatMap((t) => [{ role: "user", content: t.q }, { role: "assistant", content: t.answer.slice(0, 4000) }]);
    setTurns((ts) => [...ts, { q: query, items: [], answer: "", running: true }]);
    setQ("");
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      const r = await fetch("/api/agent", { method: "POST", signal: ctrl.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query, history, map_state: mapState }) });
      if (!r.ok || !r.body) throw new Error(`The agent endpoint returned HTTP ${r.status}.`);
      const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += value;
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const p of parts) if (p.startsWith("data: ")) handle(JSON.parse(p.slice(6)));
      }
      patch((t) => (t.running ? { ...t, running: false, error: "The stream ended before the agent finished." } : t));
    } catch (err) {
      const stopped = (err as Error).name === "AbortError";
      patch((t) => ({ ...t, running: false, error: stopped ? "Stopped." : (err as Error).message }));
    }
  }

  if (!open) {
    return <button onClick={() => setOpen(true)} className="pill pill-solid absolute bottom-6 left-4 z-10">Nemotron agent{running ? " · running" : ""}</button>;
  }

  const s = turns.at(-1)?.stats;
  return (
    <section aria-label="Nemotron map agent" className="absolute bottom-4 left-4 top-16 z-10 flex w-[350px] flex-col overflow-hidden rounded-2xl bg-[#101014]/95 text-white">
      <header className="border-b border-white/10 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="eyebrow flex items-center gap-2"><i className={`h-2 w-2 rounded-full ${!enabled ? "bg-white/30" : running ? "animate-pulse bg-[#76b900]" : "bg-[#76b900]"}`} />Nemotron map agent</div>
          <button onClick={() => setOpen(false)} className="text-[12px] text-white/60 hover:text-white">Hide</button>
        </div>
        <div className="mt-1 truncate font-mono text-[11px] text-white/50" title={model}>{model}</div>
        {s && (
          <dl className="mt-3 grid grid-cols-4 gap-2 font-mono text-[11px]">
            {[["steps", s.steps], ["tools", s.tool_calls], ["ttft", `${s.ttft_ms}ms`], ["total", `${(s.total_ms / 1000).toFixed(1)}s`], ["in tok", s.prompt_tokens.toLocaleString()], ["out tok", s.completion_tokens.toLocaleString()], ["tok/s", s.total_ms ? Math.round(s.completion_tokens / (s.total_ms / 1000)) : "–"]].map(([k, v]) => (
              <div key={k}><dt className="text-white/40">{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        )}
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
        {!enabled && <p className="text-[13px] leading-5 text-white/75">The agent is offline. Add <code className="font-mono">NVIDIA_API_KEY</code> to <code className="font-mono">backend/.env</code> and restart the API.</p>}
        {enabled && turns.length === 0 && (
          <div>
            <p className="text-[13px] leading-5 text-white/75">Give it a job. It queries the data, drives the map, and shows every step here.</p>
            <div className="mt-2">{SAMPLES.map((x) => <button key={x} onClick={() => ask(x)} className="block w-full border-t border-white/10 py-2 text-left text-[13px] leading-[18px] hover:text-white/70">{x}</button>)}</div>
          </div>
        )}
        {turns.map((t, i) => (
          <article key={i} className="space-y-2">
            <p className="text-[13px] font-medium leading-5">{t.q}</p>
            {t.items.map((it, j) => <Trace key={j} item={it} />)}
            {t.running && !t.answer && <div className="eyebrow text-white/50">Working…</div>}
            {t.answer && <p className="whitespace-pre-wrap text-[13px] leading-5 text-white/85">{t.answer.replace(/\*\*/g, "")}</p>}
            {t.error && <p role="alert" className="text-[13px] leading-5 text-[#ff9a7a]">{t.error}</p>}
          </article>
        ))}
        <div ref={end} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="border-t border-white/10 p-2">
        <div className="flex items-center gap-2 rounded-full border border-white/25 py-1 pl-3 pr-1">
          <input value={q} onChange={(e) => setQ(e.target.value)} disabled={!enabled} placeholder="Tell the agent what to do" aria-label="Instruction for the map agent" className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-white/40" />
          {running
            ? <button type="button" onClick={() => abort.current?.abort()} className="pill pill-light !py-1">Stop</button>
            : <button disabled={!enabled || !q.trim()} className="pill pill-light !py-1 disabled:opacity-30">Run</button>}
        </div>
      </form>
    </section>
  );
}
