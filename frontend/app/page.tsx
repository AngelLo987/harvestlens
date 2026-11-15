"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Logo from "@/components/Logo";
import ProductPreview from "@/components/ProductPreview";
import SignalChart from "@/components/SignalChart";
import { getJSON, type Meta } from "@/lib/api";

const esri = (bbox: string, w: number, h: number) =>
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=${bbox}&bboxSR=4326&imageSR=3857&size=${w},${h}&format=jpg&f=image`;

const PIVOTS = esri("-101.25,37.72,-100.65,37.98", 2000, 1100); // center-pivot farmland, southwest Kansas
const VALLEY = esri("-121.95,38.55,-121.65,38.72", 2000, 1400); // Yolo County, California

const SOURCES = ["USDA Cropland Data Layer", "Copernicus Sentinel-2", "Copernicus Sentinel-1", "Google Earth Engine", "NVIDIA Nemotron"];

const AUDIENCES = [
  { name: "USDA analysts", body: "Ask for every high-confidence silage field in a county, then open any one of them to check its time series and the evidence behind its label.", points: ["County answers in seconds", "Per-field evidence and intervals", "Anomalies flagged for review"] },
  { name: "Crop statisticians", body: "Compare silage and grain area across counties and export field-level results, with every model feature included, as CSV or GeoJSON.", points: ["Cross-county comparison", "CSV and GeoJSON export", "All 13 features per field"] },
  { name: "Policy teams", body: "See harvest type by region at a glance, with a plain count of how many fields still need a person to look at them.", points: ["Regional summary dashboard", "Silage share by area", "Open about uncertainty"] },
];

const STEPS = [
  ["01", "Ingest", "Pull Sentinel-2 optical and Sentinel-1 radar for each field across the season, masked to corn with the Cropland Data Layer."],
  ["02", "Extract", "Find the harvest date and measure greenness, canopy moisture and post-harvest residue. Radar fills in when clouds hide the cut."],
  ["03", "Classify", "A 12-member gradient-boosting ensemble returns P(silage) and a 90% interval for every field."],
  ["04", "Ask", "Query the results in plain English, drill into a field, and export what you need."],
];

function Panel({ eyebrow, title, body, children, className, top, image }: {
  eyebrow: string; title: string; body: string; children: React.ReactNode; className: string; top: string; image?: string;
}) {
  return (
    <section className={`relative overflow-hidden rounded-[20px] text-white lg:sticky ${top} ${className}`}>
      {image && (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={image} alt="" className="absolute inset-0 h-full w-full object-cover" />
          <div className="absolute inset-0 bg-gradient-to-r from-black/85 via-black/70 to-black/40" />
        </>
      )}
      <div className="relative grid min-h-[600px] items-center gap-12 p-8 md:p-14 lg:grid-cols-[1fr_1.05fr]">
        <div>
          <div className="eyebrow text-white/70">{eyebrow}</div>
          <h2 className="font-display mt-5 text-[40px] leading-[1.08] tracking-[-0.02em] md:text-[52px]">{title}</h2>
          <p className="mt-5 max-w-md text-[17px] leading-7 text-white/80">{body}</p>
        </div>
        <div>{children}</div>
      </div>
    </section>
  );
}

export default function Landing() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [open, setOpen] = useState(0);
  const [flagged, setFlagged] = useState<{ q: string; a: string } | null>(null);

  useEffect(() => {
    getJSON<Meta>("/api/meta").then(setMeta).catch(() => {});
    const q = "Which fields in Marathon County are uncertain?";
    fetch("/api/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) })
      .then((r) => r.json()).then((d) => setFlagged({ q, a: d.answer })).catch(() => {});
  }, []);

  const cm = meta?.model.confusion.matrix;

  return (
    <div className="bg-white">
      <div className="bg-black px-4 py-2 text-center text-[13px] text-white">
        Demo build: field data is simulated. Real Sentinel ingestion through Earth Engine is next.
      </div>

      <header className="sticky top-0 z-40 bg-white">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-10 px-6 md:px-10">
          <Link href="/" aria-label="Cornea home"><Logo /></Link>
          <nav className="hidden gap-7 text-[14px] md:flex">
            <a href="#signal" className="hover:text-muted">The signal</a>
            <a href="#who" className="hover:text-muted">Who it serves</a>
            <a href="#how" className="hover:text-muted">How it works</a>
          </nav>
          <Link href="/app" className="pill pill-solid ml-auto">Open the map</Link>
        </div>
      </header>

      <main>
        <div className="mx-auto max-w-[1400px] px-6 pb-16 pt-20 text-center md:px-10 md:pt-28">
          <h1 className="font-display text-[44px] leading-[1.04] tracking-[-0.025em] md:text-[96px]">Silage or grain.<br />Field by field.</h1>
          <p className="mx-auto mt-7 max-w-xl text-[18px] leading-7 text-body">
            Cornea reads corn fields from satellite radar and optical time series, so USDA analysts can answer a county question in under a minute.
          </p>
        </div>

        <div className="mx-auto max-w-[1400px] px-4 md:px-10">
          <div className="relative overflow-hidden rounded-[20px] bg-forest px-4 py-14 md:px-20 md:py-20">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={PIVOTS} alt="Satellite view of center-pivot farmland in southwest Kansas" className="absolute inset-0 h-full w-full object-cover" />
            <div className="relative"><ProductPreview /></div>
          </div>
          <p className="eyebrow mt-2 text-right text-muted">Imagery © Esri, Maxar, Earthstar Geographics</p>
        </div>

        <div className="mx-auto max-w-[1400px] px-6 py-20 md:px-10">
          <p className="text-center text-[17px]">Designed around open, public data</p>
          <ul className="font-display mt-9 flex flex-wrap items-center justify-center gap-x-14 gap-y-4 text-[20px] tracking-[-0.01em] text-body">
            {SOURCES.map((s) => <li key={s}>{s}</li>)}
          </ul>
        </div>

        <div id="signal" className="mx-auto max-w-[1400px] scroll-mt-20 space-y-4 px-4 md:px-10">
          <Panel top="lg:top-20" className="bg-forest" eyebrow="The signal" title="Silage is cut green. Grain dries down first."
            body="Silage is chopped in late summer while the canopy is still alive, then the ground goes bare. Grain senesces for weeks and is combined in fall, leaving stover that radar can still see.">
            <SignalChart />
          </Panel>

          <Panel top="lg:top-24" className="bg-[#101014]" eyebrow="The model" title="Confidence you can audit."
            body="Every label ships with a probability, a 90% interval and the signals behind it. When members of the ensemble disagree or clouds hide the harvest, the field is flagged for a person.">
            <div className="border-t border-white/20">
              {[
                [meta ? `${(meta.model.accuracy * 100).toFixed(1)}%` : "–", `Held-out accuracy on ${meta?.model.n_validation ?? "–"} simulated fields`],
                [cm ? `${cm[0][1] + cm[1][0]}` : "–", "Misclassified in that validation set"],
                [meta ? String(meta.model.features.length) : "–", "Phenology features per field"],
              ].map(([v, k]) => (
                <div key={k} className="flex items-baseline justify-between gap-6 border-b border-white/20 py-6">
                  <span className="font-display text-[56px] leading-none tracking-[-0.02em]">{v}</span>
                  <span className="max-w-[220px] text-right text-[14px] leading-5 text-white/70">{k}</span>
                </div>
              ))}
            </div>
          </Panel>

          <Panel top="lg:top-28" className="bg-forest" image={VALLEY} eyebrow="The copilot" title="Ask it like you'd ask a colleague."
            body="The copilot turns a question into filters and answers only from numbers the pipeline computed. It never invents field data.">
            <div className="rounded-xl bg-white p-6 text-ink">
              <div className="eyebrow text-muted">You</div>
              <p className="mt-1 text-[16px] leading-6">{flagged?.q ?? "…"}</p>
              <div className="eyebrow mt-5 text-muted">Cornea</div>
              <p className="mt-1 text-[16px] leading-6 text-body">{flagged?.a ?? "Reading fields…"}</p>
              <Link href="/app" className="pill pill-solid mt-6">Try your own question</Link>
            </div>
          </Panel>
        </div>

        <div id="who" className="mx-auto grid max-w-[1400px] scroll-mt-20 gap-12 px-6 py-28 md:px-10 lg:grid-cols-2 lg:gap-20">
          <div className="plot-grid flex min-h-[460px] items-center justify-center rounded-[20px] p-8">
            <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-[0_12px_40px_rgb(16_40_29/0.14)]">
              <div className="eyebrow text-muted">{AUDIENCES[open].name}</div>
              <ul className="mt-4">
                {AUDIENCES[open].points.map((p) => <li key={p} className="border-t border-line py-3 text-[15px]">{p}</li>)}
              </ul>
            </div>
          </div>
          <div>
            <h2 className="font-display text-[40px] leading-[1.1] tracking-[-0.02em] md:text-[48px]">Built for the people who report the harvest</h2>
            <div className="mt-10">
              {AUDIENCES.map((a, i) => (
                <div key={a.name} className="border-b border-ink">
                  <button onClick={() => setOpen(i)} aria-expanded={open === i} className="font-display w-full py-4 text-left text-[26px] tracking-[-0.01em]">{a.name}</button>
                  {open === i && <p className="max-w-lg pb-6 text-[16px] leading-7 text-body">{a.body}</p>}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div id="how" className="mx-auto max-w-[1400px] scroll-mt-20 px-6 pb-28 md:px-10">
          <h2 className="font-display text-[40px] leading-[1.1] tracking-[-0.02em] md:text-[48px]">From pixels to a county report</h2>
          <ol className="mt-12 grid gap-10 md:grid-cols-2 lg:grid-cols-4">
            {STEPS.map(([n, t, b]) => (
              <li key={n} className="border-t border-ink pt-4">
                <div className="eyebrow text-muted">{n}</div>
                <h3 className="font-display mt-8 text-[26px] tracking-[-0.01em]">{t}</h3>
                <p className="mt-3 text-[15px] leading-6 text-body">{b}</p>
              </li>
            ))}
          </ol>
        </div>
      </main>

      <footer className="bg-forest text-white">
        <div className="mx-auto max-w-[1400px] px-6 py-24 text-center md:px-10">
          <h2 className="font-display text-[44px] leading-[1.08] tracking-[-0.02em] md:text-[60px]">See this season from above.</h2>
          <p className="mx-auto mt-5 max-w-lg text-[17px] leading-7 text-white/75">Four counties, about 1,800 demo fields, and every classification open to inspection.</p>
          <Link href="/app" className="pill pill-light mt-9">Open the map</Link>
        </div>
        <div className="border-t border-white/15">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-4 px-6 py-6 md:px-10">
            <Logo light size={18} />
            <span className="eyebrow text-white/60">USDA Hack the Harvest entry · Not a USDA product</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
