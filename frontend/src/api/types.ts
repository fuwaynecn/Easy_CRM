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
