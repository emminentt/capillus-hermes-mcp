export function toDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

export function ageSeconds(value: string | null | undefined, now = new Date()): number | null {
  const parsed = toDate(value);
  if (!parsed) {
    return null;
  }
  return Math.max(0, Math.round((now.getTime() - parsed.getTime()) / 1000));
}

export function localDate(value: string | Date, timeZone: string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);
  const get = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function dateRangeEndingToday(days: number, timeZone: string, now = new Date()): string[] {
  const dates: string[] = [];
  const localToday = localDate(now, timeZone);
  const cursor = new Date(`${localToday}T12:00:00.000Z`);
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(cursor);
    d.setUTCDate(cursor.getUTCDate() - i);
    dates.push(localDate(d, "UTC"));
  }
  return dates;
}
