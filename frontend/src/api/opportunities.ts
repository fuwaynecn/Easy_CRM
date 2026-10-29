/**
 * 机会的 HTTP 契约层。
 *
 * 三个请求体各自「缺什么」是刻意的，而且不是注释约定、是类型约定：
 * - 创建体没有 `owner_id`：机会负责人从客户负责人继承（后端 `extra="forbid"`，
 *   多传就是 422）。
 * - 更新体没有 `stage`：阶段只能经 `/opportunities/{id}/stage` 变更，那里才会写
 *   `stage_histories`；PATCH 带上 stage 同样是 422。
 * - 终态字段（`won_at` / `lost_reason_id` / `lost_note`）只在阶段变更体里出现。
 *
 * 金额一律用 `string`：后端 numeric(14,2) 在线上是定点字符串，前端不该把它变成
 * JS number 再变回去——那正是 0.1+0.2 类误差进业务的地方。
 */

import { api } from "./client";
import type { Board, LostReason, OpenStage, Opportunity, Page } from "./types";

export interface OpportunityListParams {
  keyword?: string;
  stage?: string;
  customer_id?: number;
  page?: number;
  page_size?: number;
  sort?: string;
}

export function listOpportunities(params: OpportunityListParams) {
  const query = new URLSearchParams();
  if (params.keyword) query.set("keyword", params.keyword);
  if (params.stage) query.set("stage", params.stage);
  if (params.customer_id !== undefined) query.set("customer_id", String(params.customer_id));
  if (params.sort) query.set("sort", params.sort);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.page_size ?? 20));
  return api.get<Page<Opportunity>>(`/api/v1/opportunities?${query.toString()}`);
}

/**
 * 创建体：**没有 `owner_id`**——机会负责人从客户负责人继承（后端 schema
 * `extra="forbid"` 会 422）。`amount` 用字符串，避免浮点在网络上来回转换。
 */
export interface OpportunityCreateBody {
  customer_id: number;
  name: string;
  /** 新建只允许四个推进阶段；传 won/lost 后端 400。 */
  stage?: OpenStage;
  amount?: string;
  expected_close_date?: string | null;
  competitor?: string | null;
  win_rate?: number | null;
}

/**
 * 更新体：**没有 `stage` / `owner_id` / `won_at` / `lost_reason_id`**。
 * 阶段变更必须走 `changeStage`（后端只在那条路径上写 stage_histories），
 * 类型层面就把这条规则固化下来，比注释可靠。
 */
export interface OpportunityUpdateBody {
  name?: string;
  amount?: string;
  expected_close_date?: string | null;
  competitor?: string | null;
  win_rate?: number | null;
}

export interface StageChangeBody {
  stage: string;
  /**
   * 变更备注 / 重开原因，写进 `stage_histories.note`。
   * 与 `lost_note`（输单备注，写进机会）**不是一回事**，不要混用。
   */
  note?: string | null;
  /**
   * 进 `lost` 时必填。目标是任何非 `lost` 阶段时必须显式传 `null`：
   * 后端对「非 lost 却带非空输单字段」直接 400。
   */
  lost_reason_id?: number | null;
  lost_note?: string | null;
}

export function createOpportunity(body: OpportunityCreateBody) {
  return api.post<Opportunity>("/api/v1/opportunities", body);
}

export function getOpportunity(id: number) {
  return api.get<Opportunity>(`/api/v1/opportunities/${id}`);
}

export function updateOpportunity(id: number, body: OpportunityUpdateBody) {
  return api.patch<Opportunity>(`/api/v1/opportunities/${id}`, body);
}

/** 软删（后端写 `deleted_at`），204 无 body；权限只给 manager/admin。 */
export function deleteOpportunity(id: number) {
  return api.del(`/api/v1/opportunities/${id}`);
}

/** 阶段变更的唯一入口：每次调用后端都会写一条 stage_histories。 */
export function changeStage(id: number, body: StageChangeBody) {
  return api.post<Opportunity>(`/api/v1/opportunities/${id}/stage`, body);
}

export function getBoard(params: { customer_id?: number } = {}) {
  const query = new URLSearchParams();
  if (params.customer_id !== undefined) query.set("customer_id", String(params.customer_id));
  const suffix = query.toString();
  return api.get<Board>(`/api/v1/opportunities/board${suffix ? `?${suffix}` : ""}`);
}

/** 输单原因下拉的数据源：后端只返回启用中的条目，按 sort_order 排好。 */
export function listLostReasons() {
  return api.get<LostReason[]>("/api/v1/lost-reasons");
}
