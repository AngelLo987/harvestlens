"use client";

import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import { useEffect, useRef } from "react";

type FC = GeoJSON.FeatureCollection;

/** One marker per county (centroid of its fields) so results are visible at national zoom. */
function countyPoints(fc: FC | null): FC {
  const acc = new Map<string, { x: number; y: number; n: number; sil: number }>();
  for (const f of fc?.features ?? []) {
    const p = f.properties as { county_id: string; label: string };
    const ring = (f.geometry as GeoJSON.Polygon).coordinates[0];
    const a = acc.get(p.county_id) ?? { x: 0, y: 0, n: 0, sil: 0 };
    a.x += ring[0][0]; a.y += ring[0][1]; a.n += 1; a.sil += p.label === "silage" ? 1 : 0;
    acc.set(p.county_id, a);
  }
  return { type: "FeatureCollection", features: [...acc.entries()].map(([id, a]) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [a.x / a.n, a.y / a.n] },
    properties: { county_id: id, n: a.n, share: a.sil / a.n },
  })) };
}

const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    imagery: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
      maxzoom: 18,
    },
  },
  layers: [{ id: "imagery", type: "raster", source: "imagery", paint: { "raster-saturation": -0.35, "raster-brightness-max": 0.85 } }],
};

// Cornea covers the United States only: the camera cannot leave this box (Hawaii and Alaska to Puerto Rico and Maine).
const US_BOUNDS: [[number, number], [number, number]] = [[-180, 10], [-50, 75]];

export default function MapView({
  data, selected, onSelect, fitTo, highlight, padLeft = 0,
}: {
  highlight: FC | null;
  padLeft?: number;
  data: FC | null;
  selected: string | null;
  onSelect: (id: string) => void;
  fitTo: [number, number, number, number] | null;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const ready = useRef(false);
  const pending = useRef<FC | null>(null);
  const fitRef = useRef(fitTo);
  fitRef.current = fitTo;
  const lastFit = useRef(0);
  const padRef = useRef(padLeft);
  padRef.current = padLeft;
  // Keep fitted content clear of the agent panel, but never squeeze a narrow map to nothing.
  const padding = (m: MLMap) => ({ top: 70, right: 60, bottom: 60, left: Math.max(60, Math.min(padRef.current + 40, m.getContainer().clientWidth - 260)) });
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const m = new maplibregl.Map({ container: el.current!, style: structuredClone(STYLE), center: [-95, 41], zoom: 3.6, maxBounds: US_BOUNDS, attributionControl: { compact: true } });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });

    m.once("style.load", () => {
      m.addSource("fields", { type: "geojson", data: pending.current ?? { type: "FeatureCollection", features: [] } });
      const color: maplibregl.ExpressionSpecification = ["match", ["get", "label"], "silage", "#f08a4b", "#7c9bff"];
      m.addSource("county-pts", { type: "geojson", data: countyPoints(pending.current) });
      m.addLayer({
        id: "county-pts", type: "circle", source: "county-pts", maxzoom: 9,
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "n"], 1, 7, 500, 18],
          "circle-color": ["interpolate", ["linear"], ["get", "share"], 0, "#7c9bff", 0.5, "#c4b5a8", 1, "#f08a4b"],
          "circle-stroke-color": "#fff", "circle-stroke-width": 2, "circle-opacity": 0.95,
        },
      });
      m.addLayer({
        id: "fields-fill", type: "fill", source: "fields",
        paint: { "fill-color": color, "fill-opacity": ["interpolate", ["linear"], ["get", "confidence"], 0.5, 0.25, 1, 0.62] },
      });
      m.addLayer({
        id: "fields-line", type: "line", source: "fields",
        paint: { "line-color": "#ffffff", "line-width": 1, "line-opacity": 0.85 },
      });
      m.addLayer({
        id: "fields-flag", type: "line", source: "fields",
        filter: [">", ["length", ["get", "flags"]], 0],
        paint: { "line-color": "#f7e463", "line-width": 2, "line-dasharray": [2, 1.2] },
      });
      m.addLayer({
        id: "fields-selected", type: "line", source: "fields", filter: ["==", ["get", "id"], ""],
        paint: { "line-color": "#ffffff", "line-width": 3.5 },
      });
      m.addSource("agent", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "agent-fill", type: "fill", source: "agent", paint: { "fill-color": "#76b900", "fill-opacity": 0.3 } });
      m.addLayer({ id: "agent-line", type: "line", source: "agent", paint: { "line-color": "#a6e22e", "line-width": 3 } });
      ready.current = true;
      if (fitRef.current) m.fitBounds(fitRef.current, { padding: padding(m), duration: 0, maxZoom: 15 });
      pending.current = null;
    });

    m.on("mousemove", "fields-fill", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      m.getCanvas().style.cursor = "pointer";
      const p = f.properties as Record<string, unknown>;
      const flags = JSON.parse(String(p.flags ?? "[]")) as string[];
      popup.setLngLat(e.lngLat).setHTML(
        `<div style="font-weight:600">${p.id} · <span style="color:${p.label === "silage" ? "#b45f06" : "#1e4e8c"}">${String(p.label).toUpperCase()}</span></div>
         <div>Confidence ${(Number(p.confidence) * 100).toFixed(0)}% · ${p.area_ha} ha</div>
         <div>Harvest ${p.harvest_date}</div>${flags.length ? `<div style="color:#a16207">⚠ ${flags.length} review flag(s)</div>` : ""}`,
      ).addTo(m);
    });
    m.on("mouseleave", "fields-fill", () => { m.getCanvas().style.cursor = ""; popup.remove(); });
    m.on("mouseenter", "county-pts", (e) => {
      m.getCanvas().style.cursor = "pointer";
      const p = e.features?.[0]?.properties as { county_id: string; n: number; share: number };
      popup.setLngLat(e.lngLat).setHTML(`<b>${p.county_id}</b><div>${p.n} fields · ${(p.share * 100).toFixed(0)}% silage</div><div style="color:#666">Click to zoom in</div>`).addTo(m);
    });
    m.on("mouseleave", "county-pts", () => { m.getCanvas().style.cursor = ""; popup.remove(); });
    m.on("click", "county-pts", (e) => {
      const g = e.features?.[0]?.geometry as GeoJSON.Point;
      m.flyTo({ center: g.coordinates as [number, number], zoom: 11 });
    });
    m.on("click", "agent-fill", (e) => {
      const id = e.features?.[0]?.properties?.id;
      if (id) onSelectRef.current(String(id));
    });
    m.on("click", "fields-fill", (e) => {
      const id = e.features?.[0]?.properties?.id;
      if (id) onSelectRef.current(String(id));
    });
    // Side panels change the map's size without a window resize. If that happens right after
    // a programmatic fit, redo the fit so the target stays centered in the visible area.
    const observer = new ResizeObserver(() => {
      m.resize();
      if (fitRef.current && Date.now() - lastFit.current < 3000) m.fitBounds(fitRef.current, { padding: padding(m), duration: 300, maxZoom: 15 });
    });
    observer.observe(el.current!);
    return () => { observer.disconnect(); m.remove(); };
  }, []);

  useEffect(() => {
    if (!data) return;
    if (!ready.current) { pending.current = data; return; }
    (map.current!.getSource("fields") as GeoJSONSource).setData(data);
    (map.current!.getSource("county-pts") as GeoJSONSource).setData(countyPoints(data));
  }, [data]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => m.setFilter("fields-selected", ["==", ["get", "id"], selected ?? ""]);
    if (ready.current) apply(); else m.once("style.load", apply);
  }, [selected]);

  useEffect(() => {
    if (fitTo && map.current) {
      lastFit.current = Date.now();
      map.current.fitBounds(fitTo, { padding: padding(map.current), duration: 900, maxZoom: 15 });
    }
  }, [fitTo]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ready.current) (map.current!.getSource("agent") as GeoJSONSource).setData(highlight ?? { type: "FeatureCollection", features: [] });
  }, [highlight]);

  return (
    <div className="relative h-full w-full">
      <div ref={el} className="h-full w-full" />
      <div className="eyebrow absolute left-4 top-4 flex items-center gap-4 rounded-full bg-white px-4 py-2">
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-silage" /> Silage</span>
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-grain" /> Grain</span>
        <span className="flex items-center gap-1.5"><i className="h-0 w-3 border-t-2 border-dashed border-yellow-500" /> Review</span>
        {highlight && <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#76b900]" /> Agent</span>}
        <span className="text-muted" title="Fill opacity shows model confidence">Opacity = confidence</span>
      </div>
    </div>
  );
}
