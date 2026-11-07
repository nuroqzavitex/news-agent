import type { Metadata } from "next";
import { Be_Vietnam_Pro } from "next/font/google";
import Link from "next/link";
import { Suspense } from "react";

import { AgentStatus, AgentStatusFallback } from "@/components/agent-status";
import { MainNav } from "@/components/main-nav";

import "./globals.css";

const beVietnam = Be_Vietnam_Pro({
  variable: "--font-be-vietnam",
  subsets: ["vietnamese", "latin"],
  weight: ["400", "500", "700"],
});

export const metadata: Metadata = {
  title: { default: "News Agent", template: "%s | News Agent" },
  description: "Bảng vận hành của News Agent: tin tức tiếng Việt cho mạng multi-agent MI4304",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="vi" className={`${beVietnam.variable} antialiased`}>
      <body className="min-h-dvh">
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4">
            <Link href="/news" className="text-lg font-bold tracking-tight">
              News Agent
            </Link>
            <Suspense fallback={<div className="h-5 w-32" />}>
              <MainNav />
            </Suspense>
            <div className="ml-auto">
              <Suspense fallback={<AgentStatusFallback />}>
                <AgentStatus />
              </Suspense>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-5 py-8">{children}</main>
      </body>
    </html>
  );
}
