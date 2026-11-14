"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { doyToDate, FLAG_TEXT, type FieldDetail } from "@/lib/api";

const MONTH_TICKS = [91, 121, 152, 182, 213, 244, 274, 305];

function merge(doy: number[], cols: Record<string, (number | null)[]>) {
  return doy.map((d, i) => ({ doy: d, ...Object.fromEntries(Object.entries(cols).map(([k, v]) => [k, v[i]])) }));
}

function ndviColor(v: number | null) {
  if (v == null) return "#cbd5e1";
  const t = Math.max(0, Math.min(1, (v - 0.1) / 0.8));
  const r = Math.round(166 - t * 140), g = Math.round(97 + t * 90), b = Math.round(26 + t * 20);
  return `rgb(${r},${g},${b})`;
}

export default function FieldPanel({ field, onClose, onAsk }: { field: FieldDetail; onClose: () => void; onAsk: (q: string) => void }) {
  const opt = merge(field.series.s2_doy, { NDVI: field.series.ndvi, NDWI: field.series.ndwi });
  const sar = merge(field.series.s1_doy, { VH: field.series.vh, VV: field.series.vv });
  const pct = (x: number) => `${(x * 100).toFixed(0)}%`;
  const isSilage = field.label === "silage";
  const cloudFree = field.series.ndvi.filter((v) => v != null).length;
  // monthly NDVI chips (best available scene per month)
  const months = [4, 5, 6, 7, 8, 9, 10, 11].map((m) => {
    const idx = field.series.s2_doy.map((d, i) => [d, i] as const).filter(([d]) => {
      const dt = new Date(Date.UTC(2025, 0, d));
      return dt.getUTCMonth() + 1 === m;
    });
    const vals = idx.map(([, i]) => field.series.ndvi[i]).filter((v): v is number => v != null);
    return { m, v: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null };
  });

  return (
    <aside className="flex h-full w-[360px] shrink-0 xl:w-[440px] flex-col overflow-y-auto bg-white">
      <div className="sticky top-0 z-10 flex items-start justify-between border-b border-neutral-100 bg-white px-5 py-4">
        <div>
          <div className="eyebrow text-muted">Field · {field.county_id}</div>
          <div className="font-display mt-1 text-[32px] leading-none tracking-[-0.02em]">{field.id}</div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span className="inline-flex items-center gap-1.5 font-medium capitalize"><i className={`h-2.5 w-2.5 rounded-full ${isSilage ? "bg-silage" : "bg-grain"}`} />{field.label}</span>
            <span className="text-neutral-400">·</span>
            <span className="text-neutral-700">{pct(field.confidence)} confidence</span>
            <span className="text-neutral-400">·</span>
            <span className="text-neutral-700">{field.area_ha} ha</span>
          </div>
        </div>
        <button onClick={onClose} aria-label="Close field panel" className="rounded-md px-2 py-1 text-lg text-neutral-500 hover:bg-neutral-100">✕</button>
      </div>

      <section className="px-5 pt-4">
        <div className="mb-1 flex justify-between text-xs text-neutral-600">
          <span>P(silage) = {field.prob_silage.toFixed(2)}</span>
          <span>90% interval {field.interval[0].toFixed(2)}–{field.interval[1].toFixed(2)}</span>
        </div>
        <div className="relative h-3 rounded-full bg-gradient-to-r from-grain to-silage">
          <div className="absolute top-0 h-3 rounded-full border-2 border-white/90 bg-white/30"
            style={{ left: `${field.interval[0] * 100}%`, width: `${Math.max(1, (field.interval[1] - field.interval[0]) * 100)}%` }} />
          <div className="absolute -top-1 h-5 w-1 rounded bg-ink" style={{ left: `calc(${field.prob_silage * 100}% - 2px)` }} />
        </div>
        <div className="mt-0.5 flex justify-between text-[10px] text-neutral-400"><span>grain</span><span>silage</span></div>
      </section>

      {field.flags.length > 0 && (
        <section className="mx-5 mt-4 rounded-xl bg-wash p-4 text-[14px]">
          <div className="eyebrow">Needs analyst review</div>
          <ul className="mt-2 space-y-1 text-body">{field.flags.map((f) => <li key={f}>{FLAG_TEXT[f] ?? f}</li>)}</ul>
          <button onClick={() => onAsk(`Explain ${field.id}`)} className="mt-2 text-[13px] font-medium underline underline-offset-2">Ask the copilot why</button>
        </section>
      )}

      <section className="px-5 pt-5">
        <h3 className="eyebrow text-muted">Evidence</h3>
        <ul className="mt-2">
          {field.evidence.map((e) => (
            <li key={e.signal} className="border-t border-line py-3">
              <div className="flex items-center justify-between gap-3 text-[13px]">
                <span className="font-medium">{e.signal}</span>
                <span className="inline-flex shrink-0 items-center gap-1.5 font-mono text-[12px]"><i className={`h-2 w-2 rounded-full ${e.supports === "silage" ? "bg-silage" : "bg-grain"}`} />{e.value} {e.supports}</span>
              </div>
              <p className="mt-1 text-[13px] leading-5 text-body">{e.detail}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="px-5 pt-5">
        <h3 className="eyebrow text-muted">Sentinel-2 optical time series</h3>
        <p className="text-xs text-neutral-500">{cloudFree}/{field.series.s2_doy.length} cloud-free acquisitions · dashed line = detected harvest ({field.harvest_date})</p>
        <div className="h-44">
          <ResponsiveContainer>
            <LineChart data={opt} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="#eee" />
              <XAxis dataKey="doy" type="number" domain={[91, 334]} ticks={MONTH_TICKS} tickFormatter={doyToDate} fontSize={10} />
              <YAxis domain={[-0.4, 1]} fontSize={10} />
              <Tooltip labelFormatter={(d) => doyToDate(Number(d))} formatter={(v: number) => v?.toFixed(2)} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <ReferenceLine x={field.harvest_doy} stroke="#10231a" strokeDasharray="4 3" />
              <Line dataKey="NDVI" stroke="#2f855a" dot={{ r: 2 }} strokeWidth={2} connectNulls isAnimationActive={false} />
              <Line dataKey="NDWI" stroke="#0891b2" dot={{ r: 2 }} strokeWidth={1.5} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="px-5 pt-3">
        <h3 className="eyebrow text-muted">Sentinel-1 radar backscatter (dB)</h3>
        <p className="text-xs text-neutral-500">Cloud-independent; used as fallback for harvest timing</p>
        <div className="h-40">
          <ResponsiveContainer>
            <LineChart data={sar} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="#eee" />
              <XAxis dataKey="doy" type="number" domain={[91, 334]} ticks={MONTH_TICKS} tickFormatter={doyToDate} fontSize={10} />
              <YAxis domain={[-26, -4]} fontSize={10} />
              <Tooltip labelFormatter={(d) => doyToDate(Number(d))} formatter={(v: number) => v?.toFixed(1)} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <ReferenceLine x={field.features.harvest_doy_sar ?? undefined} stroke="#7c3aed" strokeDasharray="4 3" />
              <Line dataKey="VH" stroke="#7c3aed" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line dataKey="VV" stroke="#a78bfa" dot={false} strokeWidth={1.5} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="px-5 pb-6 pt-3">
        <h3 className="eyebrow text-muted">Imagery</h3>
        <div className="mt-2 grid grid-cols-[140px_1fr] gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={field.thumbnail} alt={`Basemap imagery of field ${field.id}`} className="h-[140px] w-[140px] rounded-md object-cover" />
          <div>
            <div className="text-xs text-neutral-600">Monthly mean NDVI</div>
            <div className="mt-1 grid grid-cols-4 gap-1">
              {months.map(({ m, v }) => (
                <div key={m} className="flex h-12 flex-col items-center justify-center rounded text-[10px] font-medium text-white" style={{ background: ndviColor(v) }}
                  title={v == null ? "No cloud-free scene" : `NDVI ${v.toFixed(2)}`}>
                  <span>{new Date(Date.UTC(2025, m - 1, 1)).toLocaleString("en-US", { month: "short", timeZone: "UTC" })}</span>
                  <span>{v == null ? "cloud" : v.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <p className="mt-2 text-[10px] text-neutral-400">Thumbnail: Esri World Imagery basemap (not acquisition-dated). Source of series: {field.source}.</p>
      </section>
    </aside>
  );
}
