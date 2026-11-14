"use client";

import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { doyToDate, getJSON, type FieldDetail } from "@/lib/api";

const TICKS = [121, 182, 244, 305];
const SILAGE = "#d96c30";
const GRAIN = "#6486f5";

async function pick(type: string) {
  const fc = await getJSON<GeoJSON.FeatureCollection>(`/api/fields?county=story-ia&type=${type}&min_conf=0.97`);
  const clean = fc.features.find((f) => (f.properties as { flags: string[] }).flags.length === 0);
  return getJSON<FieldDetail>(`/api/fields/${(clean!.properties as { id: string }).id}`);
}

/** NDVI of one confidently classified silage field against one grain field. */
export default function SignalChart() {
  const [d, setD] = useState<{ s: FieldDetail; g: FieldDetail } | null>(null);
  useEffect(() => { Promise.all([pick("silage"), pick("grain")]).then(([s, g]) => setD({ s, g })).catch(() => {}); }, []);
  if (!d) return <div className="h-72" />;
  const rows = d.s.series.s2_doy.map((doy, i) => ({ doy, Silage: d.s.series.ndvi[i], Grain: d.g.series.ndvi[i] }));
  return (
    <div>
      <div className="eyebrow mb-3 text-white/60">NDVI, Apr to Nov · simulated reference fields</div>
      <div className="h-72">
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 12, left: -24, bottom: 0 }}>
            <CartesianGrid stroke="rgb(255 255 255 / 0.1)" vertical={false} />
            <XAxis dataKey="doy" type="number" domain={[91, 334]} ticks={TICKS} tickFormatter={doyToDate} tick={{ fill: "rgb(255 255 255 / 0.6)", fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 1]} ticks={[0, 0.5, 1]} tick={{ fill: "rgb(255 255 255 / 0.6)", fontSize: 11 }} axisLine={false} tickLine={false} />
            <Tooltip labelFormatter={(x) => doyToDate(Number(x))} formatter={(v: number) => v?.toFixed(2)} contentStyle={{ borderRadius: 10, border: 0, fontSize: 12, color: "#17171c" }} />
            <Legend wrapperStyle={{ fontSize: 12, color: "#fff" }} iconType="plainline" />
            <ReferenceLine x={d.s.harvest_doy} stroke={SILAGE} strokeDasharray="3 4" label={{ value: "Chopped", fill: "#fff", fontSize: 11, position: "insideTopRight" }} />
            <ReferenceLine x={d.g.harvest_doy} stroke={GRAIN} strokeDasharray="3 4" label={{ value: "Combined", fill: "#fff", fontSize: 11, position: "insideTopLeft" }} />
            <Line dataKey="Silage" stroke={SILAGE} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            <Line dataKey="Grain" stroke={GRAIN} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
