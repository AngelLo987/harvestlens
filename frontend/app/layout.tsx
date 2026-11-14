import type { Metadata } from "next";
import { DM_Mono, Inter, Schibsted_Grotesk } from "next/font/google";
import "./globals.css";

const head = Schibsted_Grotesk({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-head" });
const body = Inter({ subsets: ["latin"], variable: "--font-body" });
const code = DM_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-code" });

export const metadata: Metadata = {
  title: "HarvestLens | Silage or grain, field by field",
  description: "Field-level corn harvest classification from Sentinel-1 and Sentinel-2 time series, with a plain-English copilot.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${head.variable} ${body.variable} ${code.variable}`}>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
