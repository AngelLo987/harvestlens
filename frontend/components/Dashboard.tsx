"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Meta, Summary } from "@/lib/api";

export default function Dashboard({ summary, meta, onPickCounty }: {
  summary: { overall: Summary; counties: Record<string, Summary & { name: string; state: string }> } | null;
  meta: Meta | null;
  onPickCounty: (id: string) => void;
}) {
  if (!summary || !meta) return <div className="p-4 text-sm text-neutral-500">Loading…</div>;
  const o = summary.overall;
  const rows = Object.entries(summary.counties).map(([id, c]) => ({
    id, name: `${c.name.replace(" County", "")}${c.state ? `, ${c.state}` : ""}`,
    Silage: Math.round(c.silage_area_ha), Grain: Math.round(c.area_ha - c.silage_area_ha),
    share: c.silage_share_area, flagged: c.flagged, fields: c.fields, conf: c.mean_confidence,
  }));
  const hist = o.confidence_hist.bins.map((b, i) => ({ bin: b + "%", Fields: o.confidence_hist.counts[i] }));
  const cm = meta.model.confusion.matrix;

  return (
    <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
      <div className="grid grid-cols-2 gap-x-5">
        {[
          ["Fields classified", o.fields.toLocaleString()],
          ["Silage share (area)", `${(o.silage_share_area * 100).toFixed(0)}%`],
          ["Mean confidence", o.mean_confidence ? `${(o.mean_confidence * 100).toFixed(1)}%` : "–"],
          ["Need review", `${o.flagged} (${((o.flagged / Math.max(1, o.fields)) * 100).toFixed(0)}%)`],
        ].map(([k, v]) => (
          <div key={k} className="border-t border-ink pb-4 pt-2">
            <div className="eyebrow text-[10px] text-muted">{k}</div>
            <div className="font-display mt-2 text-[26px] leading-none tracking-[-0.02em]">{v}</div>
          </div>
        ))}
      </div>

      <div>
        <h3 className="eyebrow text-muted">Corn area by harvest type (ha)</h3>
        <div className="h-48">
          <ResponsiveContainer>
            <BarChart data={rows} layout="vertical" margin={{ left: 10, right: 10 }}>
              <CartesianGrid stroke="#eee" horizontal={false} />
              <XAxis type="number" fontSize={10} />
              <YAxis type="category" dataKey="name" width={90} fontSize={11} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="Silage" stackId="a" fill="#d9602b" stroke="#fff" strokeWidth={2} />
              <Bar dataKey="Grain" stackId="a" fill="#3d63e8" stroke="#fff" strokeWidth={2} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <table className="w-full text-xs">
        <thead className="eyebrow text-left text-[10px] text-muted"><tr><th className="py-1">County</th><th>Fields</th><th>Silage</th><th>Conf.</th><th>Review</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => onPickCounty(r.id)} className="cursor-pointer border-t border-line tabular-nums hover:bg-wash">
              <td className="py-1.5 font-medium">{r.name}</td><td>{r.fields}</td><td>{(r.share * 100).toFixed(0)}%</td>
              <td>{r.conf ? (r.conf * 100).toFixed(0) + "%" : "–"}</td><td>{r.flagged}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div>
        <h3 className="eyebrow text-muted">Model confidence distribution</h3>
        <div className="h-36">
          <ResponsiveContainer>
            <BarChart data={hist} margin={{ left: -20, right: 10 }}>
              <CartesianGrid stroke="#eee" vertical={false} />
              <XAxis dataKey="bin" fontSize={10} />
              <YAxis fontSize={10} />
              <Tooltip />
              <Bar dataKey="Fields" fill="#17171c" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="border-t border-ink pt-3 text-xs">
        <div className="eyebrow text-muted">Model validation (simulated labels)</div>
        <div className="mt-1 text-neutral-600">
          Held-out accuracy <b>{(meta.model.accuracy * 100).toFixed(1)}%</b> · F1 (silage) {(meta.model.f1_silage * 100).toFixed(1)}% · n={meta.model.n_validation}
          {meta.model.demo_accuracy != null && <> · map fields vs. reference labels <b>{(meta.model.demo_accuracy * 100).toFixed(1)}%</b></>}
        </div>
        <table className="mt-2 text-center">
          <thead><tr><th /><th className="px-2 text-neutral-500">pred silage</th><th className="px-2 text-neutral-500">pred grain</th></tr></thead>
          <tbody>
            <tr><td className="pr-2 text-right text-neutral-500">silage</td><td className="bg-green-50">{cm[0][0]}</td><td className="bg-red-50">{cm[0][1]}</td></tr>
            <tr><td className="pr-2 text-right text-neutral-500">grain</td><td className="bg-red-50">{cm[1][0]}</td><td className="bg-green-50">{cm[1][1]}</td></tr>
          </tbody>
        </table>
        <div className="mt-2 text-neutral-500">{meta.model.ensemble_size}-member bagged gradient-boosting ensemble on {meta.model.features.length} phenology features.
          {meta.data_source === "simulated" && " Demo data: simulated Sentinel series — run pipeline/ingest_gee.py for real imagery."}</div>
      </div>
    </div>
  );
}
