/**
 * 机会阶段的「值 → 中文」单一真值源，取值逐字对齐
 * `backend/app/modules/opportunities/models.py` 的 OPPORTUNITY_STAGES（design §3.6）。
 *
 * `STAGE_ORDER` 的顺序就是看板列的顺序，不要按标签字母序重排。
 */

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

/** 推进阶段：新建机会时只能选这几个（后端也只接受这几个）。 */
export const OPEN_STAGES = [
  "contact",
  "needs",
  "proposal",
  "negotiation",
] as const;

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

/** 日期在 wire 上是 ISO 日期/时间串；只取年月日展示，空值给破折号。 */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}