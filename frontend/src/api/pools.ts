/**
 * 公海与负责人变更的 HTTP 契约层。
 *
 * `listPool` 不传 `owner_id`：公海条目本来就没有负责人，后端 `PoolCustomerRead`
 * 也不返回 `owner_id`（用“没有这个字段”表达“无主”，避免前端把 null 当 0）。
 */

import { api } from "./client";
import type {
  AssignableUser,
  Customer,
  Page,
  PoolCustomer,
  PoolRules,
} from "./types";

export interface PoolListParams {
  keyword?: string;
  page?: number;
  page_size?: number;
}

export function listPool(params: PoolListParams = {}) {
  const query = new URLSearchParams();
  if (params.keyword) query.set("keyword", params.keyword);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.page_size ?? 20));
  return api.get<Page<PoolCustomer>>(`/api/v1/pool?${query.toString()}`);
}

export function claimCustomer(id: number) {
  // 领取返回 CustomerRead：负责人/部门/最后跟进都已回填，前端不再二次 GET。
  return api.post<Customer>(`/api/v1/pool/${id}/claim`, {});
}

export interface AssignBody {
  owner_id: number;
  reason?: string;
}

export function assignCustomer(id: number, body: AssignBody) {
  return api.post<Customer>(`/api/v1/customers/${id}/assign`, body);
}

export function releaseCustomer(id: number, body: { reason: string }) {
  return api.post<Customer>(`/api/v1/customers/${id}/release`, body);
}

export function listAssignableUsers() {
  return api.get<AssignableUser[]>("/api/v1/users/assignable");
}

export function getPoolRules() {
  return api.get<PoolRules>("/api/v1/admin/pool-rules");
}

export function updatePoolRules(body: Partial<PoolRules>) {
  return api.patch<PoolRules>("/api/v1/admin/pool-rules", body);
}
