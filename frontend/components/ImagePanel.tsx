"use client";

import { useEffect, useState } from "react";
import type { ImageResult } from "@/lib/api";

const words = (s: string) => s.replace(/_/g, " ");

/** Result of analyzing one uploaded field image: its metadata, what Nemotron read from it, and the model's call. */
export default function ImagePanel({ result, busy, error, onRerun, onClose }: {
  result: ImageResult | null; busy: boolean; error: string | null;
  onRerun: (extra: { lat?: string; lon?: string; date?: string }) => void; onClose: () => void;
}) {
  const meta = result?.metadata;
  const [lat, setLat] = useState(""), [lon, setLon] = useState(""), [date, setDate] = useState("");
  useEffect(() => { setLat(""); setLon(""); setDate(""); }, [meta?.filename]);
  const needLoc = meta?.missing.includes("location"), needDate = meta?.missing.includes("date");
  const canRerun = (!needLoc || (lat !== "" && lon !== "")) && (!needDate || date !== "");
  const field = "mt-1 w-full rounded-md border border-line px-2 py-1.5 text-[13px] outline-none focus:border-ink";

  return (
    <aside className="flex h-full w-[360px] shrink-0 flex-col overflow-y-auto bg-white xl:w-[440px]">
      <div className="sticky top-0 z-10 flex items-start justify-between border-b border-neutral-100 bg-white px-5 py-4">
        <div className="min-w-0">
          <div className="eyebrow text-muted">Image analysis</div>
          <div className="font-display mt-1 truncate text-[24px] leading-tight tracking-[-0.02em]">{meta?.filename ?? "Reading image…"}</div>
        </div>
        <button onClick={onClose} aria-label="Close image panel" className="rounded-md px-2 py-1 text-lg text-neutral-500 hover:bg-neutral-100">✕</button>
      </div>

      {error && <p role="alert" className="mx-5 mt-4 rounded-xl bg-wash p-4 text-[14px] text-red-700">{error}</p>}
      {busy && <p role="status" className="px-5 pt-4 text-[14px] text-muted">Reading the image and asking Nemotron…</p>}

      {result && meta && (
        <>
          <section className="px-5 pt-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={result.preview} alt={`Uploaded image ${meta.filename}`} className="max-h-64 w-full rounded-md bg-neutral-100 object-contain" />
            <p className="mt-1 text-xs text-neutral-500">{meta.format} · {meta.width}×{meta.height} px · {meta.bands} band{meta.bands === 1 ? "" : "s"}</p>
          </section>

          <section className="px-5 pt-5">
            <h3 className="eyebrow text-muted">Metadata</h3>
            <dl className="mt-2 text-[13px]">
              <div className="border-t border-line py-3">
                <dt className="font-medium">Location</dt>
                <dd className="mt-1 text-body">{meta.location
                  ? <>{meta.location.lat.toFixed(5)}, {meta.location.lon.toFixed(5)} <span className="text-neutral-500">· from {meta.location.source}</span>{meta.in_us === false && <span className="text-red-700"> · outside the United States</span>}</>
                  : "Not found in the file"}</dd>
              </div>
              <div className="border-t border-line py-3">
                <dt className="font-medium">Date</dt>
                <dd className="mt-1 text-body">{meta.date
                  ? <>{meta.date.value} <span className="text-neutral-500">· from {meta.date.source}</span></>
                  : "Not found in the file"}</dd>
              </div>
            </dl>
            {meta.missing.length > 0 && (
              <form className="rounded-xl bg-wash p-4 text-[13px]" onSubmit={(e) => { e.preventDefault(); onRerun({ lat, lon, date }); }}>
                <div className="eyebrow">Add the missing {meta.missing.join(" and ")}</div>
                {needLoc && (
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <label>Latitude<input className={field} type="number" step="any" min={-90} max={90} value={lat} onChange={(e) => setLat(e.target.value)} placeholder="44.90" /></label>
                    <label>Longitude<input className={field} type="number" step="any" min={-180} max={180} value={lon} onChange={(e) => setLon(e.target.value)} placeholder="-89.76" /></label>
                  </div>
                )}
                {needDate && <label className="mt-2 block">Image date<input className={field} type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>}
                <button type="submit" disabled={busy || !canRerun} className="mt-3 rounded-md border border-ink bg-white px-3 py-1.5 font-medium hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-50">Run again with these</button>
              </form>
            )}
          </section>

          <section className="px-5 pt-5">
            <h3 className="eyebrow text-muted">Read from the image by Nemotron</h3>
            {result.parameters ? (
              <>
                <ul className="mt-2 text-[13px]">
                  {result.parameter_spec.map((p) => {
                    const v = result.parameters!.values[p.name];
                    return (
                      <li key={p.name} className="flex items-center justify-between gap-3 border-t border-line py-3">
                        <span className="font-medium">{p.label}</span>
                        <span className={`font-mono text-[12px] ${v === "cannot_tell" ? "text-neutral-500" : ""}`}>{words(v)}</span>
                      </li>
                    );
                  })}
                </ul>
                {result.parameters.notes && <p className="border-t border-line pt-3 text-[13px] leading-5 text-body">{result.parameters.notes}</p>}
                <p className="mt-2 text-[10px] text-neutral-400">{result.parameters.model}. Placeholder parameters until the model's inputs are final.</p>
              </>
            ) : <p className="mt-2 border-t border-line pt-3 text-[13px] text-body">{result.parameters_error ?? "No parameters were returned."}</p>}
          </section>

          <section className="px-5 pb-6 pt-5">
            <h3 className="eyebrow text-muted">Harvest type</h3>
            {result.prediction ? (
              <p className="mt-2 border-t border-line pt-3 text-[14px]">
                <span className="inline-flex items-center gap-1.5 font-medium capitalize"><i className={`h-2.5 w-2.5 rounded-full ${result.prediction.label === "silage" ? "bg-silage" : "bg-grain"}`} />{result.prediction.label}</span>
                <span className="text-neutral-700"> · P(silage) = {result.prediction.prob_silage.toFixed(2)}</span>
              </p>
            ) : <p className="mt-2 border-t border-line pt-3 text-[13px] leading-5 text-body">{meta.in_us === false ? "Not run: the image is outside the United States." : "No model is connected yet, so this image has no silage or grain label. The team's model plugs in at backend/app/image_model.py."}</p>}
          </section>
        </>
      )}
    </aside>
  );
}
