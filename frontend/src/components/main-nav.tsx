"use client";

import Link from "next/link";
import { useSelectedLayoutSegment } from "next/navigation";

const LINKS = [
  { href: "/", segment: null, label: "Tổng quan" },
  { href: "/news", segment: "news", label: "Tin tức" },
  { href: "/ask", segment: "ask", label: "Hỏi agent" },
  { href: "/network", segment: "network", label: "Mạng agent" },
  { href: "/messages", segment: "messages", label: "Nhật ký", also: "traces" },
  { href: "/faults", segment: "faults", label: "Fault lab" },
];

export function MainNav() {
  const active = useSelectedLayoutSegment();
  return (
    <nav aria-label="Trang chính" className="flex flex-wrap gap-x-5 gap-y-2 text-sm font-medium">
      {LINKS.map(({ href, segment, label, ...rest }) => {
        const current = active === segment || ("also" in rest && active === rest.also);
        return (
        <Link
          key={href}
          href={href}
          aria-current={current ? "page" : undefined}
          className={
            current
              ? "text-ink underline decoration-action decoration-2 underline-offset-8"
              : "text-muted hover:text-ink"
          }
        >
          {label}
        </Link>
        );
      })}
    </nav>
  );
}
