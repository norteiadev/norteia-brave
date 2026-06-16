import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Providers } from "./providers";
import "./globals.css";

// UI-SPEC typography: Geist Sans for UI/prose, Geist Mono for data (IDs, JSON
// payloads, scores, tokens, USD). Exposed as CSS vars consumed by --font-sans /
// --font-mono in globals.css.
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Norteia Brave — CMS Territorial",
  description:
    "Painel de operações do pipeline Brave (Nascente → Rio → Mar): DLQ, monitor, gate WhatsApp, custos e funis.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // dark-default ops console (UI-SPEC). `suppressHydrationWarning` is required by
  // next-themes since it sets the `class` on <html> before hydration.
  return (
    <html lang="pt-BR" className="dark" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
