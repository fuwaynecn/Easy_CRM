import { api } from "./client";
import type { Contact } from "./types";

/**
 * 联系人写入体。
 *
 * 可选字段允许显式 `null`：PATCH 是局部更新语义，"不传"等于"保持不变"，用户把
 * 手机号/邮箱删空时必须传 null 才真的清得掉（后端 `ContactUpdate` 的每个可选字段
 * 都是 `str | None`，见 `backend/app/modules/contacts/schemas.py`）。
 */
export interface ContactBody {
  name: string;
  title?: string | null;
  phone?: string | null;
  wechat?: string | null;
  email?: string | null;
  is_primary?: boolean;
  decision_role?: string | null;
  remark?: string | null;
}

/**
 * 某个客户下的联系人，**不分页**：单个客户的联系人量级很小，后端直接返回
 * `list[ContactRead]`（不是 `Page[...]`）。主联系人排在第一个。
 */
export function listContacts(customerId: number) {
  return api.get<Contact[]>(`/api/v1/customers/${customerId}/contacts`);
}

export function createContact(customerId: number, body: ContactBody) {
  return api.post<Contact>(`/api/v1/customers/${customerId}/contacts`, body);
}

/**
 * 修改联系人走扁平的 `/contacts/{id}`：联系人 id 全局唯一，嵌套写路由
 * `/customers/{cid}/contacts/{id}` 已被删除（调它会 404）。
 */
export function updateContact(contactId: number, body: Partial<ContactBody>) {
  return api.patch<Contact>(`/api/v1/contacts/${contactId}`, body);
}

/** 软删除，后端返回 204（`client.ts` 对 204 返回 undefined）。 */
export function deleteContact(contactId: number) {
  return api.del(`/api/v1/contacts/${contactId}`);
}
