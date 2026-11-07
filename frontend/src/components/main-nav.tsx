"use client";

import Link from "next/link";
import { useSelectedLayoutSegment } from "next/navigation";

const LINKS = [
  { href: "/news", segment: "news", label: "Tin tức" },
  { href: "/ask", segment: "ask", label: "Hỏi agent" },
];

export function MainNav() {
  const active = useSelectedLayoutSegment();
  return (
    <nav aria-label="Trang chính" className="flex gap-5 text-sm font-medium">
      {LINKS.map(({ href, segment, label }) => (
        <Link
          key={href}
          href={href}
          aria-current={active === segment ? "page" : undefined}
          className={
            active === segment
              ? "text-ink underline decoration-action decoration-2 underline-offset-8"
              : "text-muted hover:text-ink"
          }
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
