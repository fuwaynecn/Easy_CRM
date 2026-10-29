import { api } from "./client";
import type { Customer, Page } from "./types";

export interface CustomerListParams {
  keyword?: string;
  level?: string;
  page?: number;
  page_size?: number;
}

export function listCustomers(params: CustomerListParams) {
  const query = new URLSearchParams();
  if (params.keyword) query.set("keyword", params.keyword);
  if (params.level) query.set("level", params.level);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.page_size ?? 20));
  return api.get<Page<Customer>>(`/api/v1/customers?${query.toString()}`);
}

export interface CustomerCreateBody {
  name: string;
  industry?: string;
  region?: string;
  // 创建表单暂未收集地址，但后端 CustomerCreate 与 CustomerUpdate 都接受它；
  // 详情页的就地编辑只提交 address/remark（其余字段不传 = 保持不变）。
  address?: string;
  level?: string;
  procurement_type?: string;
  budget_status?: string;
  source?: string;
  remark?: string;
  force?: boolean;
}

export function createCustomer(body: CustomerCreateBody) {
  return api.post<Customer>("/api/v1/customers", body);
}

export function getCustomer(id: number) {
  return api.get<Customer>(`/api/v1/customers/${id}`);
}

export function updateCustomer(id: number, body: Partial<CustomerCreateBody>) {
  return api.patch<Customer>(`/api/v1/customers/${id}`, body);
}
