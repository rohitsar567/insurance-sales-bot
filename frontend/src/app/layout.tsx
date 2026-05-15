import type { Metadata, Viewport } from "next";
import "./globals.css";

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
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
