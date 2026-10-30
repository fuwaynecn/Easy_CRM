/**
 * 时间显示与输入的唯一换算点。
 *
 * 后端所有时间都是**带偏移的 ISO 串**（实测 `2026-10-31T09:00:00+08:00`），而
 * "今天/已逾期/09:30 前"这类判断按 Asia/Shanghai 自然日成立（design §2 的时区约定）。
 * 若各页面自己 `toLocaleDateString()`，用户浏览器时区就会混进业务判断——同一条待办
 * 在两台机器上显示不同日期。
 *
 * 两条禁止事项（评审会看）：
 * 1. 禁止 `value.slice(0, 10)` / `slice(11, 16)`：拿的是偏移 0 的字面时间，
 *    看起来对、换时区就错（纯 `date` 列请用 `opportunityLabels.formatDateOnly`）；
 * 2. 禁止 `new Date(value).toISOString().slice(0, 16)` 喂 `datetime-local`：
 *    那是 UTC，东八区用户会看到早 8 小时。
 */

const SHANGHAI = "Asia/Shanghai";

const dayFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: SHANGHAI,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const clockFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: SHANGHAI,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  // `hour12: false` 在部分 ICU 版本里会把午夜格式化成 "24:00"，拼进
  // `datetime-local` 就是非法值。`hourCycle: "h23"` 才是 00–23。
  hourCycle: "h23",
});

function toDate(value: string): Date | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `2026-10-31`（上海日期）。 */
export function formatDay(value: string): string {
  const date = toDate(value);
  return date ? dayFormatter.format(date) : "—";
}

/** `09:30`（上海时刻）。 */
export function formatClock(value: string): string {
  const date = toDate(value);
  return date ? clockFormatter.format(date) : "—";
}

/** `2026-10-31 09:30`（上海）。 */
export function formatDateTime(value: string): string {
  const date = toDate(value);
  return date ? `${dayFormatter.format(date)} ${clockFormatter.format(date)}` : "—";
}

/**
 * 转成 `<input type="datetime-local">` 的取值：`2026-10-31T09:30`。
 * 用上海时区的年月日时分拼装，而不是 UTC 切片。
 */
export function toDateTimeInput(value: string): string {
  const date = toDate(value);
  if (!date) return "";
  return `${dayFormatter.format(date)}T${clockFormatter.format(date)}`;
}

/** `datetime-local` 的输入值 → 后端要的 ISO 串（按东八区解释）。 */
export function fromDateTimeInput(value: string): string {
  const date = new Date(`${value}:00+08:00`);
  return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

/** 新建待办的默认到期时间：上海时区的「明天 09:00」。 */
export function defaultDueInput(): string {
  const today = dayFormatter.format(new Date()); // YYYY-MM-DD（上海）
  const next = new Date(`${today}T00:00:00+08:00`);
  next.setUTCDate(next.getUTCDate() + 1); // 用 UTC 日期推进，避开夏令时/本地时区
  return `${dayFormatter.format(next)}T09:00`;
}