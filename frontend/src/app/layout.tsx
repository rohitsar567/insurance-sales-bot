import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Insurance Sales Portfolio Expert",
  description:
    "Voice-first AI advisor for Indian health insurance. Built for Sarvam AI.",
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
