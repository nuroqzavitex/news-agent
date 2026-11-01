const TZ = "Asia/Ho_Chi_Minh";

const timeFmt = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const dateTimeFmt = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const hourFmt = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, hour: "2-digit", hourCycle: "h23" });
const dayKeyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });

export function formatHour(iso: string): string {
  return `${Number.parseInt(hourFmt.format(new Date(iso)), 10)}h`;
}

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTimeFmt.format(new Date(iso)) : "không rõ thời gian";
}

/** "07:12" for today (Vietnam time), "07:12, 31/10" otherwise. */
export function formatPublished(iso: string | null | undefined, now: Date): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (dayKeyFmt.format(d) === dayKeyFmt.format(now)) return timeFmt.format(d);
  const day = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, day: "2-digit", month: "2-digit" }).format(d);
  return `${timeFmt.format(d)}, ${day}`;
}

export function formatAgo(iso: string | null | undefined, now: Date): string {
  if (!iso) return "chưa chạy lần nào";
  const minutes = Math.round((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  return `${Math.floor(hours / 24)} ngày trước`;
}
