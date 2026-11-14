"use client";

import { useEffect, useState } from "react";
import Logo from "./Logo";
import { getJSON, type Meta } from "@/lib/api";

const QUESTION = "Show all high-confidence silage fields in Yolo County";
const W = 1000;

type Poly = { d: string; label: string; opacity: number };

/** Static, real-data preview of the app: live API results drawn over a satellite export. */
export default function ProductPreview() {
  const [polys, setPolys] = useState<Poly[]>([]);
  const [img, setImg] = useState<{ url: string; h: number } | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);

  useEffect(() => {
    (async () => {
      const meta = await getJSON<Meta>("/api/meta");
      const c = meta.counties.find((x) => x.id === "yolo-ca");
      if (!c?.bbox) return;
      const [w, s, e, n] = c.bbox;
      const h = Math.round((W * (n - s)) / ((e - w) * Math.cos(((n + s) / 2) * (Math.PI / 180))));
      setImg({
        h,
        url: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=${w},${s},${e},${n}&bboxSR=4326&imageSR=3857&size=${W},${h}&format=jpg&f=image`,
      });
      const fc = await getJSON<GeoJSON.FeatureCollection>("/api/fields?county=yolo-ca");
      setTotal(fc.features.length);
      setPolys(fc.features.map((f) => {
        const ring = (f.geometry as GeoJSON.Polygon).coordinates[0];
        const p = f.properties as { label: string; confidence: number };
        return {
          d: "M" + ring.map(([x, y]) => `${(((x - w) / (e - w)) * W).toFixed(1)} ${(((n - y) / (n - s)) * h).toFixed(1)}`).join("L") + "Z",
          label: p.label,
          opacity: 0.35 + (p.confidence - 0.5) * 0.9,
        };
      }));
      const r = await fetch("/api/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: QUESTION }) });
      setAnswer(((await r.json()) as { answer: string }).answer);
    })().catch(() => {});
  }, []);

  return (
    <div className="grid overflow-hidden rounded-xl bg-white text-left shadow-[0_24px_70px_rgb(0_0_0/0.4)] md:grid-cols-[300px_1fr]">
      <div className="flex flex-col border-r border-line p-5">
        <Logo size={18} />
        <div className="eyebrow mt-6 text-muted">You</div>
        <p className="mt-1 text-[14px] leading-5">{QUESTION}</p>
        <div className="eyebrow mt-5 text-muted">HarvestLens</div>
        <p className="mt-1 min-h-24 text-[14px] leading-5 text-body">{answer ?? "Reading fields…"}</p>
        <div className="mt-auto border-t border-line pt-3">
          <div className="eyebrow text-muted">Yolo County, CA</div>
          <div className="font-display mt-1 text-3xl tracking-[-0.02em]">{total ?? "–"} <span className="text-base text-muted">fields</span></div>
        </div>
      </div>
      <div className="relative min-h-[420px] bg-forest">
        {img && (
          <svg viewBox={`0 0 ${W} ${img.h}`} preserveAspectRatio="xMidYMid slice" className="absolute inset-0 h-full w-full" role="img" aria-label="Satellite map of Yolo County with simulated corn fields colored by predicted harvest type">
            <image href={img.url} width={W} height={img.h} />
            {polys.map((p, i) => (
              <path key={i} d={p.d} fill={p.label === "silage" ? "#f08a4b" : "#7c9bff"} fillOpacity={p.opacity} stroke="#fff" strokeOpacity={0.8} strokeWidth={0.8} />
            ))}
          </svg>
        )}
        <div className="eyebrow absolute bottom-3 left-3 flex gap-4 rounded-full bg-white px-3 py-1.5">
          <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-silage" /> Silage</span>
          <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-grain" /> Grain</span>
        </div>
      </div>
    </div>
  );
}
