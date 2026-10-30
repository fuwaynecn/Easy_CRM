/**
 * 跟进方式与待办状态的中文标签。
 *
 * 与 `opportunityLabels.ts` 分开：机会阶段和跟进方式是两套枚举，混在一个文件里
 * 迟早会出现"给 stage 用了 call 的标签"这种错配。
 */

export const ACTIVITY_TYPE_OPTIONS = [
  "call",
  "visit",
  "wechat",
  "email",
  "note",
  "other",
] as const;

export const ACTIVITY_TYPE_LABELS: Record<string, string> = {
  call: "电话",
  visit: "拜访",
  wechat: "微信",
  email: "邮件",
  note: "备注",
  other: "其他",
};

export const FOLLOW_UP_STATUS_LABELS: Record<string, string> = {
  pending: "待办",
  done: "已完成",
  cancelled: "已取消",
};

/** 未知枚举值退回原值：后端加了新类型时界面显示英文码，而不是空白。 */
export function activityTypeLabel(value: string | null | undefined): string {
  if (!value) return "";
  return ACTIVITY_TYPE_LABELS[value] ?? value;
}