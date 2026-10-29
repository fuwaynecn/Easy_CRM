/**
 * 机会阶段的「值 → 中文」单一真值源，取值逐字对齐
 * `backend/app/modules/opportunities/models.py` 的 OPPORTUNITY_STAGES（design §3.6）。
 *
 * `STAGE_ORDER` 的顺序就是看板列的顺序，不要按标签字母序重排。
 */

import type { OpenStage } from "../api/types";

export const STAGE_LABELS: Record<string, string> = {
  contact: "初步接触",
  needs: "需求确认",
  proposal: "方案报价",
  negotiation: "商务谈判",
  won: "赢单",
  lost: "输单",
};

export const STAGE_ORDER = [
  "contact",
  "needs",
  "proposal",
  "negotiation",
  "won",
  "lost",
] as const;

/**
 * 推进阶段：新建机会时只能选这几个（后端也只接受这几个）。
 *
 * 标注成 `readonly OpenStage[]` 而不是 `as const` 元组：字面量联合的唯一真值源
 * 在 `api/types.ts`（`types.ts` 不能反过来依赖常量，否则成为循环）。这里的元素
 * 一旦与 `OpenStage` 不一致，`tsc` 直接报错。
 */
export const OPEN_STAGES: readonly OpenStage[] = [
  "contact",
  "needs",
  "proposal",
  "negotiation",
];

/** 类型谓词：把任意阶段字符串（含终态）收窄到推进阶段，避免到处写 `as`。 */
export function isOpenStage(stage: string): stage is OpenStage {
  return (OPEN_STAGES as readonly string[]).includes(stage);
}

export function isTerminalStage(stage: string): boolean {
  return stage === "won" || stage === "lost";
}

/** 阶段徽标的配色：终态用实色，推进阶段用浅色，避免看板上"赢了"和"在谈"同色。 */
export const STAGE_BADGE_CLASS: Record<string, string> = {
  contact: "bg-slate-100 text-slate-700",
  needs: "bg-sky-50 text-sky-700",
  proposal: "bg-indigo-50 text-indigo-700",
  negotiation: "bg-amber-50 text-amber-800",
  won: "bg-green-100 text-green-800",
  lost: "bg-slate-200 text-slate-600",
};

/**
 * 金额展示：千分位 + 两位小数。
 *
 * `Number()` 只用于**展示**（toLocaleString），不参与任何计算；计算与比较一律
 * 交给后端的 Decimal。金额在 wire 上是字符串，就是为了不让浮点误差进到业务里。
 */
export function formatAmount(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  return numeric.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * 只取日期列（`date`）的年月日，空值给破折号。
 *
 * **仅用于 `date` 列**（如 `expected_close_date`）：`slice(0, 10)` 对纯日期是恒等
 * 操作。`datetime` 列（`won_at` / `created_at` / `updated_at`）是带偏移的 ISO 串，
 * 直接截前 10 位拿到的是 **UTC** 日期——CST 凌晨 0–8 点的事件会显示成前一天。
 * 格式化 datetime 必须先按 `Asia/Shanghai` 换算再取日期，不要复用本函数。
 */
export function formatDateOnly(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}
