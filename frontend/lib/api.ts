export type Label = "silage" | "grain";

export interface County { id: string; name: string; state: string; center: [number, number]; bbox: [number, number, number, number] | null }
export interface Meta {
  counties: County[];
  model: { accuracy: number; f1_silage: number; demo_accuracy: number | null; n_train: number; n_validation: number; ensemble_size: number; features: string[]; confusion: { labels: string[]; matrix: number[][] } };
  llm: { enabled: boolean; model: string };
  data_source: string;
}
export interface Filters { county: string[]; type: Label | null; min_conf: number | null; max_conf: number | null; anomalies: boolean }
export const EMPTY_FILTERS: Filters = { county: [], type: null, min_conf: null, max_conf: null, anomalies: false };

export interface Evidence { signal: string; value: string; supports: Label; detail: string }
export interface FieldDetail {
  id: string; county_id: string; label: Label; prob_silage: number; interval: [number, number]; confidence: number;
  area_ha: number; harvest_date: string; harvest_doy: number; flags: string[]; evidence: Evidence[]; source: string;
  features: Record<string, number | null>; thumbnail: string; polygon: [number, number][];
  series: { s2_doy: number[]; ndvi: (number | null)[]; ndwi: (number | null)[]; s1_doy: number[]; vv: number[]; vh: number[] };
}
export interface Summary {
  fields: number; silage_fields: number; grain_fields: number; area_ha: number; silage_area_ha: number;
  silage_share_area: number; mean_confidence: number | null; flagged: number;
  confidence_hist: { bins: string[]; counts: number[] };
}

export function qs(f: Filters): string {
  const p = new URLSearchParams();
  if (f.county.length) p.set("county", f.county.join(","));
  if (f.type) p.set("type", f.type);
  if (f.min_conf != null) p.set("min_conf", String(f.min_conf));
  if (f.max_conf != null) p.set("max_conf", String(f.max_conf));
  if (f.anomalies) p.set("anomalies", "true");
  return p.toString();
}

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const FLAG_TEXT: Record<string, string> = {
  low_confidence: "Low confidence (< 75%)",
  model_disagreement: "Ensemble members disagree (wide interval)",
  cloud_gaps: "Sparse cloud-free optical data — radar used as fallback",
  conflicting_signals: "Spectral evidence conflicts with the label",
  optical_sar_timing_mismatch: "Optical and radar harvest dates disagree",
};

export const doyToDate = (d: number) => {
  const dt = new Date(Date.UTC(2025, 0, 1));
  dt.setUTCDate(d);
  return dt.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
};

export interface ImageResult {
  metadata: {
    filename: string; format: string; width: number; height: number; bands: number; missing: ("location" | "date")[]; in_us: boolean | null;
    location: { lat: number; lon: number; bbox: [number, number, number, number] | null; source: string } | null;
    date: { value: string; source: string } | null;
  };
  parameters: { values: Record<string, string>; notes: string; model: string } | null;
  parameters_error: string | null;
  parameter_spec: { name: string; label: string; values: string[] }[];
  prediction: { label: Label; prob_silage: number } | null;
  preview: string;
}
