import type { Metadata, Viewport } from "next";
import { Fraunces, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

// Display face — Fraunces. A warm, high-contrast "old-style" serif with
// optical sizing. Gives the landing an editorial, trustworthy character
// (right for a serious financial decision) and immediately breaks the
// generic system-font / Inter "AI" look. Used only for the hero + section
// headings via the --font-display CSS variable.
const fraunces = Fraunces({
  subsets: ["latin"],
  // Fraunces is a variable font: omit `weight` so the full weight axis is
  // available (next/font forbids `axes` alongside an explicit `weight`).
  axes: ["opsz", "SOFT", "WONK"],
  style: ["normal", "italic"],
  variable: "--font-display",
  display: "swap",
});

// Body / UI face — Plus Jakarta Sans. A clean, slightly geometric grotesque
// with friendly curves; reads well at small sizes for chat + UI chrome.
const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Insurance Sales Portfolio Expert",
  description:
    "Voice-first AI advisor for Indian health insurance. Built for Sarvam AI.",
};

// V4 #5 — iOS soft-keyboard handling. `viewport-fit=cover` lets the page
// extend under safe-area insets (notch / home indicator) so the chat scroll
// container can use `env(safe-area-inset-bottom)` and `100dvh` to avoid
// being pushed behind the soft keyboard.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`h-full antialiased ${fraunces.variable} ${jakarta.variable}`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
