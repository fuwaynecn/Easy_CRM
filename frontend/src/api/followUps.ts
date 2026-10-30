/**
 * 待办的 HTTP 契约层。
 *
 * 两个"不该有"的字段是类型层面的规则：
 * - 没有 `owner_id`：待办跟客户走（spec §3.9），"我的待办"由 `mine=true` 表达，
 *   后端按 `customers.owner_id` 过滤。
 * - 更新体没有 `status: "done"`：完成只能走 `completeFollowUp`（后端对 PATCH
 *   传 done 直接 400），那条路径才会可选地写跟进并刷新客户新鲜度。
 */

import { api } from "./client";
import type { FollowUp, FollowUpDoneResult, FollowUpStatus, Page } from "./types";

export interface FollowUpCreateBody {
  customer_id: number;
  due_at: string;
  content: string;
  contact_id?: number | null;
  opportunity_id?: number | null;
}

export interface FollowUpUpdateBody {
  due_at?: string;
  content?: string;
  status?: Extract<FollowUpStatus, "pending" | "cancelled">;
}

export interface FollowUpListParams {
  customer_id?: number;
  status?: FollowUpStatus;
  /** true = 只看"我的客户"名下的待办 */
  mine?: boolean;
  page?: number;
  page_size?: number;
}

export function listFollowUps(params: FollowUpListParams = {}) {
  const query = new URLSearchParams();
  if (params.customer_id !== undefined) query.set("customer_id", String(params.customer_id));
  if (params.status) query.set("status", params.status);
  if (params.mine) query.set("mine", "true");
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.page_size ?? 20));
  return api.get<Page<FollowUp>>(`/api/v1/follow_ups?${query.toString()}`);
}

export function createFollowUp(body: FollowUpCreateBody) {
  return api.post<FollowUp>("/api/v1/follow_ups", body);
}

export function updateFollowUp(id: number, body: FollowUpUpdateBody) {
  return api.patch<FollowUp>(`/api/v1/follow_ups/${id}`, body);
}

/**
 * 完成待办。`activity_type` **只在勾选写跟进时传**：不勾选却传它，后端会 400
 * （"未勾选写跟进时不需要指定跟进方式"），所以这里用可选参数而不是默认值。
 */
export function completeFollowUp(
  id: number,
  body: { create_activity?: boolean; activity_type?: string },
) {
  return api.post<FollowUpDoneResult>(`/api/v1/follow_ups/${id}/done`, body);
}

export function deleteFollowUp(id: number) {
  return api.del(`/api/v1/follow_ups/${id}`);
}