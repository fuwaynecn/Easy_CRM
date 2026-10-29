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
