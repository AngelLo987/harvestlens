"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Copilot, { SAMPLE_QUERIES, type CopilotResponse } from "@/components/Copilot";
import Dashboard from "@/components/Dashboard";
import FieldPanel from "@/components/FieldPanel";
import Link from "next/link";
import Logo from "@/components/Logo";
import MapAgent, { type MapAction } from "@/components/MapAgent";
import { EMPTY_FILTERS, getJSON, qs, type FieldDetail, type Filters, type Meta, type Summary } from "@/lib/api";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

type SummaryResp = { overall: Summary; counties: Record<string, Summary & { name: string; state: string }> };

export default function Home() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [geo, setGeo] = useState<GeoJSON.FeatureCollection | null>(null);
  const [summary, setSummary] = useState<SummaryResp | null>(null);
  const [selected, setSelected] = useState<FieldDetail | null>(null);
  const [tab, setTab] = useState<"copilot" | "dashboard">("copilot");
  const [fit, setFit] = useState<[number, number, number, number] | null>(null);
  const [onboard, setOnboard] = useState(false);
  const [external, setExternal] = useState<{ q: string; n: number } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<GeoJSON.FeatureCollection | null>(null);
  const [railHidden, setRailHidden] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadMeta = useCallback(() => getJSON<Meta>("/api/meta").then(setMeta), []);
  useEffect(() => {
    loadMeta();
    if (!localStorage.getItem("hl-onboarded")) setOnboard(true);
  }, [loadMeta]);

  useEffect(() => {
    const q = qs(filters);
    getJSON<GeoJSON.FeatureCollection>(`/api/fields?${q}`).then(setGeo);
    getJSON<SummaryResp>(`/api/summary${filters.county.length ? `?county=${filters.county.join(",")}` : ""}`).then(setSummary);
  }, [filters]);

  const fitCounties = useCallback((ids: string[]) => {
    if (!meta) return;
    const boxes = meta.counties.filter((c) => (ids.length ? ids.includes(c.id) : c.id !== "uploaded") && c.bbox).map((c) => c.bbox!);
    if (!boxes.length) return;
    setFit([Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1])), Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))]);
  }, [meta]);

  const skipFit = useRef(false);
  useEffect(() => {
    if (!meta) return;
    if (skipFit.current) { skipFit.current = false; return; }
    fitCounties(filters.county);
  }, [meta, filters.county, fitCounties]);

  const selectField = useCallback(async (id: string) => {
    const f = await getJSON<FieldDetail>(`/api/fields/${id}`);
    setSelected(f);
    return f;
  }, []);

  const download = (format: "csv" | "geojson", f: Filters = filters) => {
    const a = document.createElement("a");
    a.href = `/api/export?format=${format}&${qs(f)}`;
    a.download = `harvestlens_fields.${format}`;
    a.click();
  };

  function onMapAction(a: MapAction) {
    if (a.type === "filters") {
      skipFit.current = true; // the agent's action carries its own camera target
      setFilters(a.filters);
      if (a.bbox) setFit(a.bbox);
    } else if (a.type === "fly") {
      if (a.bbox) setFit(a.bbox);
    } else if (a.type === "highlight") {
      setHighlight(a.geojson);
      if (a.bbox) setFit(a.bbox);
    } else if (a.type === "open_field") {
      setRailHidden(true); // keep the map wide enough for the agent panel and the field panel together
      selectField(a.field_id);
    } else {
      setHighlight(null);
      setSelected(null);
      setRailHidden(false);
      setFilters(EMPTY_FILTERS);
    }
  }

  function onCopilot(r: CopilotResponse) {
    const a = r.action;
    if (!a) return;
    if (a.type === "select_field" && a.field_id) {
      selectField(a.field_id).then((f) => {
        if (!(filters.county.length === 1 && filters.county[0] === f.county_id && !filters.type && filters.min_conf == null && !filters.anomalies)) {
          skipFit.current = filters.county.join(",") !== f.county_id;
          setFilters({ ...EMPTY_FILTERS, county: [f.county_id] });
        }
        const xs = f.polygon.map((p) => p[0]), ys = f.polygon.map((p) => p[1]);
        setFit([Math.min(...xs) - 0.01, Math.min(...ys) - 0.01, Math.max(...xs) + 0.01, Math.max(...ys) + 0.01]);
      });
      return;
    }
    const f = a.filters ?? {};
    const next: Filters = {
      county: f.county ? String(f.county).split(",").filter(Boolean) : [],
      type: (f.type as Filters["type"]) ?? null,
      min_conf: (f.min_conf as number | null) ?? null,
      max_conf: (f.max_conf as number | null) ?? null,
      anomalies: Boolean(f.anomalies),
    };
    setFilters(next);
    if (a.type === "compare") setTab("dashboard");
    if (a.type === "export") download((a.export_format as "csv" | "geojson") ?? "csv", next);
  }

  async function onUpload(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) { setToast(data.detail ?? "Upload failed"); return; }
    await loadMeta();
    setFilters({ ...EMPTY_FILTERS, county: ["uploaded"] });
    setToast(`Classified ${data.fields} uploaded field(s). Series simulated — connect Earth Engine for real imagery.`);
  }

  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 6000); return () => clearTimeout(t); } }, [toast]);

  const count = geo?.features.length ?? 0;
  const activeChips = useMemo(() => {
    const chips: string[] = [];
    if (filters.county.length && meta) chips.push(filters.county.map((id) => meta.counties.find((c) => c.id === id)?.name ?? id).join(", "));
    if (filters.type) chips.push(filters.type);
    if (filters.min_conf != null) chips.push(`≥${Math.round(filters.min_conf * 100)}% conf.`);
    if (filters.max_conf != null) chips.push(`≤${Math.round(filters.max_conf * 100)}% conf.`);
    if (filters.anomalies) chips.push("needs review");
    return chips;
  }, [filters, meta]);

  return (
    <div className="flex h-screen flex-col">
      {meta?.data_source === "simulated" && (
        <div className="shrink-0 bg-black px-4 py-1.5 text-center text-[12px] text-white">Demo build: field data is simulated Sentinel-1/2 series, not real imagery.</div>
      )}
      <header className="flex min-h-16 shrink-0 flex-wrap items-center gap-x-6 gap-y-2 bg-white px-5">
        <Link href="/" aria-label="HarvestLens home"><Logo /></Link>
        <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-2 text-[14px]">
          <select aria-label="County" value={filters.county[0] ?? ""} onChange={(e) => setFilters({ ...filters, county: e.target.value ? [e.target.value] : [] })}
            className="cursor-pointer bg-transparent py-1 text-[14px] outline-none">
            <option value="">All counties</option>
            {meta?.counties.map((c) => <option key={c.id} value={c.id}>{c.name}{c.state ? `, ${c.state}` : ""}</option>)}
          </select>
          <button onClick={() => fileRef.current?.click()} className="hover:text-muted" title="Upload field boundaries (GeoJSON polygons)">Upload boundaries</button>
          <input ref={fileRef} type="file" accept=".geojson,.json,application/geo+json" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }} />
          <button onClick={() => setOnboard(true)} className="hover:text-muted">Help</button>
          <button onClick={() => download("csv")} className="pill pill-line">Export CSV</button>
          <button onClick={() => download("geojson")} className="pill pill-solid">Export GeoJSON</button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className={`w-[320px] shrink-0 flex-col bg-white lg:w-[380px] ${railHidden ? "hidden" : "flex"}`}>
          <div className="flex gap-6 border-b border-line px-5">
            {(["copilot", "dashboard"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`eyebrow -mb-px border-b py-3 ${tab === t ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink"}`}>{t}</button>
            ))}
          </div>
          <div className="border-b border-line px-5 py-4">
            <div className="flex items-baseline gap-2">
              <span className="font-display text-[32px] leading-none tracking-[-0.02em]">{count.toLocaleString()}</span>
              <span className="text-[14px] text-muted">fields</span>
              {activeChips.length > 0 && <button onClick={() => setFilters(EMPTY_FILTERS)} className="ml-auto text-[13px] underline underline-offset-2">Clear</button>}
            </div>
            {activeChips.length > 0 && <div className="eyebrow mt-2 text-muted">{activeChips.join(" · ")}</div>}
            <div className="mt-3 flex flex-wrap gap-1">
              {([null, "silage", "grain"] as const).map((t) => (
                <button key={String(t)} onClick={() => setFilters({ ...filters, type: t })} aria-pressed={filters.type === t}
                  className={`rounded-full border px-2.5 py-1 text-[13px] capitalize ${filters.type === t ? "border-ink bg-ink text-white" : "border-line hover:border-ink"}`}>{t ?? "All"}</button>
              ))}
              <button onClick={() => setFilters({ ...filters, min_conf: filters.min_conf ? null : 0.9 })} aria-pressed={!!filters.min_conf}
                className={`rounded-full border px-2.5 py-1 text-[13px] ${filters.min_conf ? "border-ink bg-ink text-white" : "border-line hover:border-ink"}`}>≥90%</button>
              <button onClick={() => setFilters({ ...filters, anomalies: !filters.anomalies })} aria-pressed={filters.anomalies}
                className={`rounded-full border px-2.5 py-1 text-[13px] ${filters.anomalies ? "border-ink bg-ink text-white" : "border-line hover:border-ink"}`}>Needs review</button>
            </div>
          </div>
          <div className={tab === "copilot" ? "flex min-h-0 flex-1 flex-col" : "hidden"}><Copilot onResult={onCopilot} external={external} /></div>
          {tab === "dashboard" && <Dashboard summary={summary} meta={meta} onPickCounty={(id) => setFilters({ ...filters, county: [id] })} />}
        </aside>

        <main className={`relative min-w-0 flex-1 pb-3 pr-3 ${railHidden ? "pl-3" : ""}`}>
          <div className="h-full overflow-hidden rounded-[20px] bg-forest">
            <MapView data={geo} selected={selected?.id ?? null} onSelect={selectField} fitTo={fit} highlight={highlight} padLeft={350} />
          </div>
          <MapAgent enabled={meta?.llm.enabled ?? false} model={meta?.llm.model ?? ""} mapState={{ ...filters, selected_field: selected?.id ?? null }} onAction={onMapAction} />
          {toast && <div role="status" className="absolute bottom-8 left-1/2 -translate-x-1/2 rounded-full bg-ink px-5 py-2 text-[14px] text-white">{toast}</div>}
        </main>

        {selected && <FieldPanel field={selected} onClose={() => { setSelected(null); setRailHidden(false); }} onAsk={(q) => { setTab("copilot"); setExternal({ q, n: Date.now() }); }} />}
      </div>

      {onboard && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby="onb-title">
          <div className="w-full max-w-xl rounded-[20px] bg-white p-8">
            <div className="eyebrow text-muted">Getting started</div>
            <h2 id="onb-title" className="font-display mt-3 text-[36px] leading-[1.1] tracking-[-0.02em]">Ask a question, or click a field.</h2>
            <dl className="mt-6 text-[15px]">
              {[["Ask", "Use the copilot in plain English, or the filters."], ["Inspect", "Click a field for its time series, evidence and interval."], ["Review", "Dashed yellow outlines need an analyst's eye."], ["Export", "Download CSV or GeoJSON, or upload your own boundaries."]].map(([k, v]) => (
                <div key={k} className="grid grid-cols-[88px_1fr] border-t border-line py-2.5"><dt className="font-medium">{k}</dt><dd className="text-body">{v}</dd></div>
              ))}
            </dl>
            <div className="eyebrow mt-4 text-muted">Start with</div>
            <div className="mt-1">
              {SAMPLE_QUERIES.slice(0, 3).map((s) => (
                <button key={s} onClick={() => { setOnboard(false); localStorage.setItem("hl-onboarded", "1"); setTab("copilot"); setExternal({ q: s, n: Date.now() }); }}
                  className="flex w-full items-center justify-between border-b border-line py-2.5 text-left text-[14px] hover:text-muted">{s}<span aria-hidden="true">→</span></button>
              ))}
            </div>
            <div className="mt-6 flex justify-end">
              <button onClick={() => { setOnboard(false); localStorage.setItem("hl-onboarded", "1"); }} className="pill pill-solid">Go to the map</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
