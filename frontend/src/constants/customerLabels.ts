/**
 * 客户 / 联系人枚举的「值 → 中文」**单一真值源**。
 *
 * 取值逐字对齐后端（改这里的值等于前后端契约变更）：
 * - `backend/app/modules/customers/models.py`
 *     CUSTOMER_LEVELS   = ("A", "B", "C")
 *     PROCUREMENT_TYPES = ("tender", "direct", "framework", "other")
 *     BUDGET_STATUSES   = ("approved", "pending", "none", "unknown")
 * - `backend/app/modules/contacts/models.py`
 *     DECISION_ROLES = ("decision_maker", "tech", "purchase", "user", "other")
 *
 * 页面只允许从这里取标签与下拉选项：此前 `BUDGET_LABEL` / `PROCUREMENT_LABEL`
 * 在列表页与详情页各抄一份、`<option>` 列表又抄第三份，改一处必漏两处。
 * 标签表的键顺序就是后端元组的顺序，也即下拉框的显示顺序。
 */

/** 枚举值 → 中文标签。后端出现前端还不认识的取值时回退成值本身，绝不显示 undefined。 */
export function labelOf(labels: Record<string, string>, value: string): string {
  return labels[value] ?? value;
}

/** 下拉选项由标签表派生：选项与标签不可能再对不上。 */
function toOptions(labels: Record<string, string>): { value: string; label: string }[] {
  return Object.entries(labels).map(([value, label]) => ({ value, label }));
}

export const LEVEL_LABELS: Record<string, string> = {
  A: "A 级",
  B: "B 级",
  C: "C 级",
};

export const BUDGET_STATUS_LABELS: Record<string, string> = {
  approved: "已立项",
  pending: "预算未批",
  none: "无预算",
  unknown: "未知",
};

export const PROCUREMENT_TYPE_LABELS: Record<string, string> = {
  tender: "招标",
  direct: "直采",
  framework: "框架协议",
  other: "其他",
};

export const DECISION_ROLE_LABELS: Record<string, string> = {
  decision_maker: "决策人",
  tech: "技术负责人",
  purchase: "采购",
  user: "使用者",
  other: "其他",
};

export const LEVEL_OPTIONS = toOptions(LEVEL_LABELS);
export const BUDGET_STATUS_OPTIONS = toOptions(BUDGET_STATUS_LABELS);
export const PROCUREMENT_TYPE_OPTIONS = toOptions(PROCUREMENT_TYPE_LABELS);
export const DECISION_ROLE_OPTIONS = toOptions(DECISION_ROLE_LABELS);