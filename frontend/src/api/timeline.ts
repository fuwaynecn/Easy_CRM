import { api } from "./client";
import type { TimelinePage } from "./types";

/**
 * 客户时间线一页。`cursor` 是后端发的不透明字符串，前端只负责原样回传
 * （P3 裁定 14）：解析它意味着前后端共享了排序键的内部格式，游标一改就双端碎。
 */
export function getTimeline(customerId: number, cursor: string | null, limit = 20) {
  const query = new URLSearchParams();
  if (cursor) query.set("cursor", cursor);
  query.set("limit", String(limit));
  return api.get<TimelinePage>(`/api/v1/customers/${customerId}/timeline?${query.toString()}`);
}