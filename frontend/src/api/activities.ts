/**
 * 跟进的 HTTP 契约层。
 *
 * 三个请求体缺什么都是刻意的：
 * - 创建体没有 `created_by` / `customer_name`：作者取自登录身份，客户名由后端回填
 *   （后端 `extra="forbid"`，多传就是 422）。
 * - 更新体没有 `customer_id` / `contact_id` / `opportunity_id`：跟进不能换客户，
 *   关联对象创建后不可改（P3 裁定 11）。
 * - `next_follow_up` 只在创建时出现：一次跟进顺手排一次下次回访，是 spec §4.5 的
 *   唯一入口；编辑跟进不再排待办，避免"改个错别字多出一条待办"。
 */

import { api } from "./client";
import type { Activity, ActivityType, Page } from "./types";

export interface ActivityCreateBody {
  customer_id: number;
  type?: ActivityType;
  content: string;
  /** 不传则由服务端取当前时间（补录时才传）；必须带时区偏移 */
  activity_at?: string;
  contact_id?: number | null;
  opportunity_id?: number | null;
  next_follow_up?: { due_at: string; content: string } | null;
}

export interface ActivityUpdateBody {
  type?: ActivityType;
  content?: string;
  activity_at?: string;
}

export interface ActivityListParams {
  customer_id?: number;
  type?: ActivityType;
  created_by?: number;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export function listActivities(params: ActivityListParams = {}) {
  const query = new URLSearchParams();
  if (params.customer_id !== undefined) query.set("customer_id", String(params.customer_id));
  if (params.type) query.set("type", params.type);
  if (params.created_by !== undefined) query.set("created_by", String(params.created_by));
  if (params.date_from) query.set("date_from", params.date_from);
  if (params.date_to) query.set("date_to", params.date_to);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.page_size ?? 20));
  return api.get<Page<Activity>>(`/api/v1/activities?${query.toString()}`);
}

export function createActivity(body: ActivityCreateBody) {
  return api.post<Activity>("/api/v1/activities", body);
}

export function updateActivity(id: number, body: ActivityUpdateBody) {
  return api.patch<Activity>(`/api/v1/activities/${id}`, body);
}

/** 软删，204 无 body。 */
export function deleteActivity(id: number) {
  return api.del(`/api/v1/activities/${id}`);
}