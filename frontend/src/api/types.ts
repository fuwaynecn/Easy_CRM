export interface User {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "manager" | "sales";
  department_id: number | null;
  status: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Customer {
  id: number;
  name: string;
  short_name: string | null;
  code: string | null;
  industry: string | null;
  region: string | null;
  address: string | null;
  website: string | null;
  scale: string | null;
  level: "A" | "B" | "C";
  source: string | null;
  procurement_type: string | null;
  budget_status: string;
  status: string;
  owner_id: number | null;
  // 后端 CustomerRead 回填负责人姓名；公海客户 owner_id 与 owner_name 同为 null。
  owner_name: string | null;
  department_id: number | null;
  last_activity_at: string | null;
  remark: string | null;
  created_at: string;
  updated_at: string;
}

export interface Contact {
  id: number;
  customer_id: number;
  name: string;
  title: string | null;
  phone: string | null;
  wechat: string | null;
  email: string | null;
  is_primary: boolean;
  decision_role: string | null;
  remark: string | null;
  created_at: string;
}

/**
 * 输单原因字典（design §3.10）。
 *
 * 只有这四个字段：后端 `LostReasonRead` 逐字如此，没有 `created_at`，也没有
 * camelCase 别名——DTO 镜像 wire 字段，保持 snake_case。
 */
export interface LostReason {
  id: number;
  name: string;
  sort_order: number;
  active: boolean;
}

/**
 * 推进阶段：**新建机会时唯一允许提交的四个阶段**。
 *
 * 单独定义而不是从 `constants/opportunityLabels.ts` 反推，是为了不让 `types.ts`
 * 依赖 constants（那会形成 types ← constants ← types 的循环）。`OPEN_STAGES`
 * 在常量侧以 `readonly OpenStage[]` 标注，两边漂移会被 tsc 挡住。
 */
export type OpenStage = "contact" | "needs" | "proposal" | "negotiation";

/** 全部六个阶段：四个推进阶段 + 两个终态。 */
export type OpportunityStage = OpenStage | "won" | "lost";

export interface Opportunity {
  id: number;
  customer_id: number;
  // 后端 OpportunityRead 回填的关联名称：列表与看板不用再按 id 二次查名
  customer_name: string;
  name: string;
  stage: OpportunityStage;
  // numeric(14,2) 在线上是定点字符串（"1234.50"），不要当 number 用
  amount: string;
  expected_close_date: string | null;
  competitor: string | null;
  owner_id: number;
  owner_name: string;
  // 生效的赢单率（手工值或阶段默认值）与"是否手工填过"
  win_rate: number;
  win_rate_manual: boolean;
  lost_reason_id: number | null;
  lost_reason_name: string | null;
  lost_note: string | null;
  won_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BoardColumn {
  stage: OpportunityStage;
  total: number;
  // 同 amount：numeric 聚合在线上仍是定点字符串
  amount_total: string;
  items: Opportunity[];
}

export interface Board {
  columns: BoardColumn[];
}

/** 跟进方式（design §3.8 的六种）。 */
export type ActivityType = "call" | "visit" | "wechat" | "email" | "note" | "other";

export interface Activity {
  id: number;
  customer_id: number;
  contact_id: number | null;
  opportunity_id: number | null;
  type: ActivityType;
  content: string;
  /**
   * 后端序列化成**带偏移的 ISO**（实测 `2026-10-31T09:00:00+08:00`，不是 `Z`）。
   * 一律 `new Date(value)` 解析；切字符串会拿到字面时间，换时区就错。
   */
  activity_at: string;
  created_by: number;
  /** 后端回填的作者姓名：列表直接渲染，避免再按 id 查用户表 */
  created_by_name: string;
  created_at: string;
  updated_at: string;
}

/** 待办状态（design §3.9）。`done` 只能经 `/follow_ups/{id}/done` 到达。 */
export type FollowUpStatus = "pending" | "done" | "cancelled";

export interface FollowUp {
  id: number;
  customer_id: number;
  /** 客户名随行返回：首页三块与待办列表都要显示，省掉按 id 二次查名 */
  customer_name: string;
  contact_id: number | null;
  opportunity_id: number | null;
  /** 含时分（带偏移的 ISO 串），显示前用 constants/datetime 换成上海时间 */
  due_at: string;
  content: string;
  status: FollowUpStatus;
  done_at: string | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
  updated_at: string;
}

/** 完成待办的返回：待办本身 + 可选的随附跟进（用户勾了"顺便写一条"才有）。 */
export interface FollowUpDoneResult {
  follow_up: FollowUp;
  activity: Activity | null;
}

/** 时间线条目类型；`owner_change` 由 P4 接入（后端已预留枚举）。 */
export type TimelineItemType = "activity" | "stage_change" | "owner_change";

export interface TimelineItem {
  type: TimelineItemType;
  id: number;
  occurred_at: string;
  operator_id: number | null;
  operator_name: string;
  /** 账号已停用：历史记录仍显示，前缀标注「已停用」 */
  operator_disabled: boolean;
  activity_type: ActivityType | null;
  content: string | null;
  contact_id: number | null;
  contact_name: string | null;
  /** 联系人已删：姓名仍显示并标注，不渲染跳转 */
  contact_deleted: boolean;
  opportunity_id: number | null;
  opportunity_name: string | null;
  opportunity_deleted: boolean;
  from_stage: string | null;
  to_stage: string | null;
  /** 阶段变更**当刻**的金额（定点字符串），不是机会现值 */
  amount_snapshot: string | null;
  /** owner_change 的动作：claim / recycle / assign / transfer / release */
  owner_change_action: string | null;
  from_owner_name: string | null;
  to_owner_name: string | null;
}

export interface TimelinePage {
  items: TimelineItem[];
  /** 不透明游标；为 null 表示没有更多。前端不得解析它 */
  next_cursor: string | null;
}

export interface FollowUpBlock {
  total: number;
  items: FollowUp[];
}

export interface PoolNoticeItem {
  id: number;
  name: string;
  owner_id: number;
  owner_name: string | null;
  last_activity_at: string;
  /** 后端按上海自然日算好的天数，前端不自己减日期 */
  days_inactive: number;
}

/** 预警阈值随响应下发：前端不复制常量（后端 P4 改成可配置后前端零改动）。 */
export interface PoolNoticeRule {
  inactive_days: number;
  recycle_notice_days: number;
  threshold_days: number;
}

export interface PoolNoticeBlock {
  total: number;
  items: PoolNoticeItem[];
  rule: PoolNoticeRule;
}

export interface HomeSummary {
  today: FollowUpBlock;
  overdue: FollowUpBlock;
  pool_notice: PoolNoticeBlock;
}

/** 公海条目：没有 owner_id（无主由字段缺失表达）。 */
export interface PoolCustomer {
  id: number;
  name: string;
  short_name: string | null;
  industry: string | null;
  level: "A" | "B" | "C";
  region: string | null;
  status: "active" | "inactive" | "lost";
  last_activity_at: string | null;
  /** 后端按上海自然日算好的天数；last_activity_at 为 null 时为 null */
  days_inactive: number | null;
  created_at: string;
}

/** 可分配用户：`owned_count` / `max_owned` 让前端先给出上限提示，后端仍会 409。 */
export interface AssignableUser {
  id: number;
  display_name: string;
  department_id: number;
  department_name: string;
  owned_count: number;
  max_owned: number;
  quota_remaining: number;
}

/* ---------------------------------------------------------------------------
 * 报表（P5）：与 `backend/app/modules/reports/schemas.py` 逐字一致。
 *
 * 金额（`amount` / `total_amount`）在 wire 上是 Decimal 序列化出的定点字符串，
 * 前端只做展示（`formatAmount`），不参与计算；比率与天数在 `total = 0` 时后端
 * 给 `null`，前端显示「—」而不是 0% 或 NaN。
 * ------------------------------------------------------------------------ */

export interface FunnelStage {
  stage: string;
  count: number;
  amount: string;
}

export interface FunnelReport {
  as_of: string;
  stages: FunnelStage[];
}

export interface DepartmentRate {
  department_id: number | null;
  department_name: string;
  won: number;
  lost: number;
  total: number;
  rate: number | null;
}

export interface WinRateReport {
  date_from: string;
  date_to: string;
  won: number;
  lost: number;
  total: number;
  rate: number | null;
  by_department: DepartmentRate[];
}

export interface CycleOwner {
  owner_id: number;
  owner_name: string;
  won_count: number;
  avg_days: number | null;
}

export interface CycleMonth {
  month: string;
  won_count: number;
  avg_days: number | null;
}

export interface CycleReport {
  date_from: string;
  date_to: string;
  won_count: number;
  avg_days: number | null;
  by_owner: CycleOwner[];
  by_month: CycleMonth[];
}

export interface PerformanceOwner {
  owner_id: number;
  owner_name: string;
  won_count: number;
  amount: string;
}

export interface PerformanceMonth {
  month: string;
  won_count: number;
  amount: string;
}

export interface PerformanceReport {
  date_from: string;
  date_to: string;
  won_count: number;
  total_amount: string;
  by_owner: PerformanceOwner[];
  by_month: PerformanceMonth[];
}

export interface ActivityOwner {
  user_id: number;
  user_name: string;
  count: number;
}

export interface ActivityBucket {
  bucket: string;
  count: number;
}

export interface ActivityReport {
  date_from: string;
  date_to: string;
  granularity: "day" | "week";
  total: number;
  by_owner: ActivityOwner[];
  series: ActivityBucket[];
}

export interface LostReasonRow {
  lost_reason_id: number;
  lost_reason_name: string;
  count: number;
  amount: string;
}

export interface LostReasonReport {
  date_from: string;
  date_to: string;
  total_count: number;
  total_amount: string;
  items: LostReasonRow[];
}

export interface CompetitorRow {
  competitor: string;
  count: number;
  amount: string;
  won: number;
  lost: number;
  rate: number | null;
}

export interface CompetitorReport {
  date_from: string;
  date_to: string;
  total_opportunities: number;
  items: CompetitorRow[];
}

/** 与后端 `PoolRuleRead` 逐字一致（snake_case，不转 camelCase）。 */
export interface PoolRules {
  inactive_days: number;
  max_owned: number;
  auto_recycle_enabled: boolean;
  protect_with_open_deal: boolean;
  protect_with_pending_followup: boolean;
  recycle_notice_days: number;
}
