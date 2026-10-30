import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, errorMessage } from "../api/client";
import { createContact, deleteContact, listContacts, updateContact } from "../api/contacts";
import type { ContactBody } from "../api/contacts";
import { getCustomer, updateCustomer } from "../api/customers";
import { completeFollowUp, listFollowUps, updateFollowUp } from "../api/followUps";
import { listOpportunities } from "../api/opportunities";
import type { Contact, Customer, FollowUp } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { ActivityFormModal } from "../components/ActivityFormModal";
import type { ActivityDraft } from "../components/ActivityFormModal";
import { Field, TextArea } from "../components/Field";
import { FollowUpDoneModal } from "../components/FollowUpDoneModal";
import { FollowUpFormModal } from "../components/FollowUpFormModal";
import { Modal } from "../components/Modal";
import { OpportunityFormModal } from "../components/OpportunityFormModal";
import { QueryError } from "../components/QueryError";
import { Timeline } from "../components/Timeline";
import {
  BUDGET_STATUS_LABELS,
  DECISION_ROLE_LABELS,
  DECISION_ROLE_OPTIONS,
  PROCUREMENT_TYPE_LABELS,
  labelOf,
} from "../constants/customerLabels";
import { formatClock, formatDay } from "../constants/datetime";
import {
  STAGE_BADGE_CLASS,
  STAGE_LABELS,
  formatAmount,
  formatDateOnly,
} from "../constants/opportunityLabels";

/**
 * 与后端 `EmailStr` 的底线一致（域名必须有点）。前端先拦一次，只为少一次
 * 422 往返并把中文提示落在邮箱输入框下方；真正的校验仍在后端。
 */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** 字段级错误：键是后端 `loc` 里的字段名，值显示在该字段下方。 */
type FieldErrors = Record<string, string | undefined>;

interface ContactFormState {
  name: string;
  title: string;
  phone: string;
  wechat: string;
  email: string;
  decision_role: string;
  remark: string;
  is_primary: boolean;
}

const EMPTY_CONTACT_FORM: ContactFormState = {
  name: "",
  title: "",
  phone: "",
  wechat: "",
  email: "",
  decision_role: "",
  remark: "",
  is_primary: false,
};

/** 后端把清空的字段存成空串，`??` 兜不住，统一按"没填"显示成破折号。 */
function textOf(value: string | null | undefined): string {
  return value !== null && value !== undefined && value.trim() !== "" ? value : "—";
}

/**
 * 待办是否已逾期。与首页「已逾期」同一口径（瞬时比较），但首页那套边界由后端算；
 * 这里只用于把详情页待办卡的时间标红，标签文案不参与任何业务判定。
 */
function isOverdue(dueAt: string): boolean {
  const due = new Date(dueAt);
  return !Number.isNaN(due.getTime()) && due.getTime() < Date.now();
}

/**
 * FastAPI 的 422 响应体是 `{code, message, detail: [{loc, msg, type}]}`（见
 * `backend/app/main.py` 的 RequestValidationError 处理器）。这里把 detail 翻成
 * "字段 → 文案"，页面才能把错误落在具体输入框下方，而不是只丢一句"参数校验失败"。
 */
function fieldErrorsOf(err: unknown): FieldErrors {
  if (!(err instanceof ApiError) || err.status !== 422) return {};
  const detail = err.detail;
  if (!Array.isArray(detail)) return {};
  const errors: FieldErrors = {};
  for (const raw of detail) {
    if (typeof raw !== "object" || raw === null) continue;
    const item = raw as { loc?: unknown; msg?: unknown };
    // 只认请求体的字段：loc 形如 ["body", "email"]，否则是路径/查询参数的错误。
    if (!Array.isArray(item.loc) || item.loc[0] !== "body") continue;
    let field: string | null = null;
    for (let i = item.loc.length - 1; i >= 1; i -= 1) {
      const part: unknown = item.loc[i];
      if (typeof part === "string") {
        field = part;
        break;
      }
    }
    if (field === null || typeof item.msg !== "string" || item.msg.trim() === "") continue;
    // 同一字段可能有多条：保留第一条，别让后面的泛化描述盖掉更具体的说明。
    if (errors[field] === undefined) errors[field] = item.msg;
  }
  return errors;
}

function contactToForm(contact: Contact): ContactFormState {
  return {
    name: contact.name,
    title: contact.title ?? "",
    phone: contact.phone ?? "",
    wechat: contact.wechat ?? "",
    email: contact.email ?? "",
    decision_role: contact.decision_role ?? "",
    remark: contact.remark ?? "",
    is_primary: contact.is_primary,
  };
}

/** 空串要显式发 null：PATCH 下"不传"是保持不变，删空的字段就永远清不掉。 */
function formToBody(form: ContactFormState): ContactBody {
  const orNull = (value: string): string | null => (value.trim() === "" ? null : value.trim());
  return {
    name: form.name.trim(),
    title: orNull(form.title),
    phone: orNull(form.phone),
    wechat: orNull(form.wechat),
    email: orNull(form.email),
    decision_role: form.decision_role === "" ? null : form.decision_role,
    remark: orNull(form.remark),
    is_primary: form.is_primary,
  };
}

export default function CustomerDetailPage() {
  const params = useParams();
  const customerId = Number(params.id);
  // 非数字 id 不能让页面去打 /customers/NaN（422），直接按"不存在"处理。
  const validId = Number.isInteger(customerId) && customerId > 0;
  const queryClient = useQueryClient();
  const { user } = useAuth();

  const [editing, setEditing] = useState(false);
  const [address, setAddress] = useState("");
  const [remark, setRemark] = useState("");
  const [customerError, setCustomerError] = useState<string | null>(null);
  const [customerFieldErrors, setCustomerFieldErrors] = useState<FieldErrors>({});
  const [saved, setSaved] = useState(false);

  const [contactModal, setContactModal] = useState<
    { mode: "create" } | { mode: "edit"; contact: Contact } | null
  >(null);
  const [contactForm, setContactForm] = useState<ContactFormState>(EMPTY_CONTACT_FORM);
  const [contactError, setContactError] = useState<string | null>(null);
  const [contactFieldErrors, setContactFieldErrors] = useState<FieldErrors>({});
  const [deleteTarget, setDeleteTarget] = useState<Contact | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [showOpportunityForm, setShowOpportunityForm] = useState(false);

  const customerQuery = useQuery({
    queryKey: ["customer", customerId],
    queryFn: () => getCustomer(customerId),
    enabled: validId,
  });

  const contactsQuery = useQuery({
    queryKey: ["contacts", customerId],
    queryFn: () => listContacts(customerId),
    // 客户本身都没读到（403/404）时不再打一次必定失败的联系人请求。
    enabled: validId && customerQuery.isSuccess,
  });

  // 机会列表的可见性继承客户：客户读不到（403/404）时这个查询也不发。
  // 查询键 `["opportunities", "customer", id]` 落在 `["opportunities"]` 前缀下，
  // OpportunityFormModal 新建成功后失效前缀即可让本页自动刷新。
  const opportunitiesQuery = useQuery({
    queryKey: ["opportunities", "customer", customerId],
    queryFn: () => listOpportunities({ customer_id: customerId, page_size: 50 }),
    enabled: validId && customerQuery.isSuccess,
  });

  // 待办的可见性继承客户（后端 `get_customer` 先判作用域）；这里只取"未完成、最近
  // 5 条"，全量在首页按天汇总——详情页不是待办管理台。
  // key 落在 `["follow_ups"]` 前缀下：写跟进/新建待办/完成待办都按这个前缀失效，
  // 这张卡因此不需要各自再通知一遍。
  // enabled 与联系人/机会同一条线：客户读不到（403/404）时不再打一次注定失败的请求。
  const followUpsQuery = useQuery({
    queryKey: ["follow_ups", customerId, "pending"],
    queryFn: () => listFollowUps({ customer_id: customerId, status: "pending", page_size: 5 }),
    enabled: validId && customerQuery.isSuccess,
  });

  const [showActivityForm, setShowActivityForm] = useState(false);
  const [activityDraft, setActivityDraft] = useState<ActivityDraft | null>(null);
  const [showFollowUpForm, setShowFollowUpForm] = useState(false);
  const [doneTarget, setDoneTarget] = useState<FollowUp | null>(null);
  const [doneError, setDoneError] = useState<string | null>(null);
  const [todoError, setTodoError] = useState<string | null>(null);

  const saveCustomer = useMutation({
    mutationFn: () => updateCustomer(customerId, { address: address.trim(), remark: remark.trim() }),
    onMutate: () => {
      setCustomerError(null);
      setCustomerFieldErrors({});
    },
    onSuccess: async (updated: Customer) => {
      // 先用服务端返回的实体回写缓存：保存成功后视图立刻是新值，不必等 refetch。
      // 紧接着 invalidate 再以服务端为准取一次（并发改动也能同步回来）。
      queryClient.setQueryData<Customer>(["customer", customerId], updated);
      setEditing(false);
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["customer", customerId] });
    },
    onError: (err) => {
      // 后端 message 优先（403 是"公海客户只读，请先领取后再修改"这类具体原因）；
      // 422 额外按字段落位，不让用户只知道"参数校验失败"。
      setCustomerFieldErrors(fieldErrorsOf(err));
      setCustomerError(errorMessage(err, "保存失败，请重试"));
    },
  });

  const saveContact = useMutation({
    mutationFn: (target: { mode: "create" } | { mode: "edit"; contact: Contact }) => {
      const body = formToBody(contactForm);
      return target.mode === "create"
        ? createContact(customerId, body)
        : updateContact(target.contact.id, body);
    },
    onMutate: () => {
      setContactError(null);
      setContactFieldErrors({});
    },
    onSuccess: async () => {
      setContactModal(null);
      setContactForm(EMPTY_CONTACT_FORM);
      // 设/取消主联系人会改掉**同客户其他联系人**的 is_primary（后端在同一事务里
      // 做，见 contacts/service.py:_clear_other_primaries）。只改本地那一条会在列表
      // 里留下两个"主要"标签，所以必须重新拉列表，不能只动本地 state。
      await queryClient.invalidateQueries({ queryKey: ["contacts", customerId] });
    },
    onError: (err) => {
      setContactFieldErrors(fieldErrorsOf(err));
      setContactError(errorMessage(err, "保存联系人失败，请重试"));
      // 409 基本是"并发设主联系人"：列表里的主联系人标记已经过时，重拉一次。
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ["contacts", customerId] });
      }
    },
  });

  const removeContact = useMutation({
    mutationFn: (contactId: number) => deleteContact(contactId),
    onSuccess: async () => {
      setDeleteTarget(null);
      await queryClient.invalidateQueries({ queryKey: ["contacts", customerId] });
    },
    onError: (err) => setDeleteError(errorMessage(err, "删除失败，请重试")),
  });

  const completeTodo = useMutation({
    mutationFn: (input: { id: number; createActivity: boolean; activityType: string }) =>
      completeFollowUp(input.id, {
        create_activity: input.createActivity,
        // 不勾选就不传 activity_type：后端对"不勾选却指定方式"返回 400
        ...(input.createActivity ? { activity_type: input.activityType } : {}),
      }),
    onSuccess: async () => {
      setDoneTarget(null);
      setDoneError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["follow_ups", customerId, "pending"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline", customerId] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
        queryClient.invalidateQueries({ queryKey: ["customer", customerId] }),
        // 勾选写跟进会推进 last_activity_at，客户列表的「最后跟进」也要跟着变
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
      ]);
    },
    onError: (err) => setDoneError(errorMessage(err, "标记完成失败")),
  });

  const cancelTodo = useMutation({
    // 取消是状态变更（cancelled），不是物理删除：历史仍可追溯
    mutationFn: (id: number) => updateFollowUp(id, { status: "cancelled" }),
    onSuccess: async () => {
      setTodoError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["follow_ups", customerId, "pending"] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
      ]);
    },
    onError: (err) => setTodoError(errorMessage(err, "取消待办失败")),
  });

  function openCreateContact() {
    setContactForm(EMPTY_CONTACT_FORM);
    setContactError(null);
    setContactFieldErrors({});
    setContactModal({ mode: "create" });
  }

  function openEditContact(contact: Contact) {
    setContactForm(contactToForm(contact));
    setContactError(null);
    setContactFieldErrors({});
    setContactModal({ mode: "edit", contact });
  }

  function updateContactForm(patch: Partial<ContactFormState>) {
    setContactForm((prev) => ({ ...prev, ...patch }));
    // 上一次提交的错误不能留到这次：改了哪个字段就只清那个字段的报错。
    setContactError(null);
    setContactFieldErrors((prev) => {
      const next = { ...prev };
      for (const key of Object.keys(patch)) next[key] = undefined;
      return next;
    });
  }

  function submitContact() {
    if (contactModal === null) return;
    const email = contactForm.email.trim();
    if (email !== "" && !EMAIL_PATTERN.test(email)) {
      setContactError(null);
      setContactFieldErrors({ email: "邮箱格式不正确，请检查后重试" });
      return;
    }
    setContactFieldErrors({});
    saveContact.mutate(contactModal);
  }

  if (!validId) {
    return (
      <div className="space-y-3">
        <p className="break-words text-sm text-red-600">客户不存在或已被删除</p>
        <Link to="/customers" className="text-sm text-brand-600 hover:underline">
          返回客户列表
        </Link>
      </div>
    );
  }

  if (customerQuery.isLoading) {
    return <p className="text-sm text-slate-500">加载中…</p>;
  }

  if (customerQuery.isError) {
    // 错误块与列表页共用 QueryError：403 只给一句中文提示，不渲染任何客户字段
    // （连公司名都不能出现），不重试、不登出、不跳登录页。
    return (
      <QueryError
        error={customerQuery.error}
        fallback="客户不存在或加载失败"
        onRetry={() => void customerQuery.refetch()}
      >
        <Link to="/customers" className="mt-2 block text-brand-600 hover:underline">
          返回客户列表
        </Link>
      </QueryError>
    );
  }

  const customer = customerQuery.data;
  if (!customer) {
    return <p className="text-sm text-slate-500">加载中…</p>;
  }

  // 公海（owner_id 为空）对 sales/manager 一律只读（backend/app/db/scope.py 的
  // can_write_customer）。前端据此先关掉写入口，别让人点进必定 403 的操作；真正的
  // 判定仍在后端，万一仍收到 403 就按后端 message 展示，不隐藏已获准看到的字段。
  const publicPool = customer.owner_id === null;
  const writable = !publicPool || user?.role === "admin";

  // 负责人姓名：CustomerRead 现已带 owner_name（后端回填负责人 display_name），
  // 公海客户的 owner_id / owner_name 同为 null。判定仍以 owner_id 为准，与列表页
  // 徽标、下面的 writable 用同一个口径；owner_name 缺失时退回 id，绝不显示「用户 #null」。
  const ownerLabel = publicPool
    ? "未分配（公海）"
    : (customer.owner_name ?? `用户 #${customer.owner_id}`);

  const contacts = contactsQuery.data ?? [];

  // 打开编辑时从服务端当前值起步（而不是上一次编辑的残留）。写成箭头常量而不是
  // function 声明：只有 const 的收窄才能带进闭包体（function 声明体会被独立分析，
  // `customer` 会退回 Customer | undefined）。
  const openEdit = () => {
    setAddress(customer.address ?? "");
    setRemark(customer.remark ?? "");
    setCustomerError(null);
    setCustomerFieldErrors({});
    setSaved(false);
    setEditing(true);
  };

  const toggleEdit = () => {
    if (!editing) {
      openEdit();
      return;
    }
    setEditing(false);
    setCustomerError(null);
    setCustomerFieldErrors({});
  };

  return (
    <div className="space-y-4">
      <Link to="/customers" className="inline-block text-sm text-brand-600 hover:underline">
        ← 返回客户列表
      </Link>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* 左栏：客户字段与就地编辑。min-w-0：grid 子项默认 min-width: auto，
            不加就能被一段不含空格的长文本顶宽，lg 布局下会溢出。 */}
        <section className="min-w-0 space-y-4 rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              {/* min-w-0 让这块能收缩，break-words 才能把无空格长串在盒内断开，
                  否则 375px 下整个页面会被客户名顶出横向滚动条。 */}
              <h1 className="break-words text-lg font-semibold text-slate-900">{customer.name}</h1>
              <p className="mt-1 break-words text-xs text-slate-500">
                {textOf(customer.industry)} · {textOf(customer.region)} · {customer.level} 级
                {publicPool ? " · 公海" : ""}
              </p>
            </div>
            {writable ? (
              <button
                type="button"
                onClick={toggleEdit}
                className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
              >
                {editing ? "取消" : "编辑"}
              </button>
            ) : (
              <span className="shrink-0 rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
                只读
              </span>
            )}
          </div>

          {publicPool && !writable ? (
            <p className="break-words text-xs text-amber-700">
              该客户在公海，销售与主管只能查看、不能修改。
            </p>
          ) : null}

          {saved && !editing ? <p className="text-xs text-green-600">已保存</p> : null}

          {editing ? (
            <div className="space-y-3">
              <Field
                label="地址"
                maxLength={255}
                value={address}
                error={customerFieldErrors.address}
                onChange={(e) => {
                  setAddress(e.target.value);
                  setCustomerFieldErrors((prev) => ({ ...prev, address: undefined }));
                }}
              />
              <div>
                <TextArea
                  label="备注"
                  rows={4}
                  value={remark}
                  onChange={(e) => {
                    setRemark(e.target.value);
                    setCustomerFieldErrors((prev) => ({ ...prev, remark: undefined }));
                  }}
                />
                {customerFieldErrors.remark ? (
                  <p className="mt-1 break-words text-xs text-red-600">
                    {customerFieldErrors.remark}
                  </p>
                ) : null}
              </div>
              {customerError ? (
                <p className="break-words text-sm text-red-600">{customerError}</p>
              ) : null}
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => saveCustomer.mutate()}
                  disabled={saveCustomer.isPending}
                  className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
                >
                  {saveCustomer.isPending ? "保存中…" : "保存"}
                </button>
              </div>
            </div>
          ) : (
            /* 375px 下单列（sm 以上两列）：两列在 375 里每列只剩 ~148px，地址/官网
               这类字段会被挤成一行两个字，可读性差；单列不会改变字段顺序。 */
            <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <div className="min-w-0">
                <dt className="text-slate-500">地址</dt>
                <dd className="mt-0.5 break-words">{textOf(customer.address)}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-slate-500">官网</dt>
                <dd className="mt-0.5 break-words">{textOf(customer.website)}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-slate-500">采购方式</dt>
                <dd className="mt-0.5 break-words">
                  {customer.procurement_type
                    ? labelOf(PROCUREMENT_TYPE_LABELS, customer.procurement_type)
                    : "—"}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-slate-500">预算状态</dt>
                <dd className="mt-0.5 break-words">
                  {labelOf(BUDGET_STATUS_LABELS, customer.budget_status)}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-slate-500">负责人</dt>
                <dd className="mt-0.5 break-words">{ownerLabel}</dd>
              </div>
              <div className="min-w-0 sm:col-span-2">
                <dt className="text-slate-500">备注</dt>
                <dd className="mt-0.5 whitespace-pre-wrap break-words">{textOf(customer.remark)}</dd>
              </div>
            </dl>
          )}
        </section>

        {/* 右栏：联系人。同样 min-w-0，宽屏下固定 380px，窄屏下单列铺满。 */}
        <section className="min-w-0 space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-slate-900">联系人</h2>
            {writable ? (
              <button
                type="button"
                onClick={openCreateContact}
                className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
              >
                + 添加
              </button>
            ) : (
              <span className="shrink-0 text-xs text-slate-500">只读</span>
            )}
          </div>

          {contactsQuery.isError ? (
            // 联系人读取失败（403 极少出现：客户可见才有联系人）时也不留旧数据。
            <p className="break-words text-sm text-red-600">
              {contactsQuery.error instanceof ApiError && contactsQuery.error.status === 403
                ? "无权查看该客户的联系人"
                : errorMessage(contactsQuery.error, "联系人加载失败")}
            </p>
          ) : (
            <>
              {/* 用 isPending（没有数据）而不是 isLoading（= pending 且在 fetch）：
                  联系人查询在客户读到之前是 disabled 的，切换那一刻 isLoading 可能还是
                  false，直接用 isLoading 会闪一下"还没有联系人"的空态。 */}
              {contactsQuery.isPending ? (
                <p className="text-sm text-slate-500">加载中…</p>
              ) : null}

              {contactsQuery.isSuccess && contacts.length === 0 ? (
                <p className="break-words rounded-md bg-slate-50 p-3 text-sm text-slate-500">
                  {writable
                    ? "还没有联系人。点右上角「+ 添加」记下决策人、采购和技术负责人，跟进时不用再翻聊天记录。"
                    : "该客户还没有联系人。"}
                </p>
              ) : null}

              <ul className="divide-y divide-slate-100">
                {contacts.map((contact) => {
                  const role = contact.decision_role
                    ? labelOf(DECISION_ROLE_LABELS, contact.decision_role)
                    : null;
                  const meta = [contact.title, role]
                    .filter((part): part is string => typeof part === "string" && part !== "")
                    .join(" · ");
                  const reachable = [
                    contact.phone,
                    contact.wechat ? `微信 ${contact.wechat}` : null,
                    contact.email,
                  ]
                    .filter((part): part is string => typeof part === "string" && part !== "")
                    .join(" · ");
                  return (
                    <li key={contact.id} className="flex items-start justify-between gap-2 py-2">
                      {/* min-w-0：联系人姓名/邮箱都可能是不含空格的长串，先让这列能
                          收缩，break-words 才有机会在盒内断行（375px 不横向滚动）。 */}
                      <div className="min-w-0 space-y-0.5">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="min-w-0 break-words text-sm font-medium text-slate-900">
                            {contact.name}
                          </span>
                          {contact.is_primary ? (
                            <span className="shrink-0 rounded bg-brand-50 px-1.5 py-0.5 text-xs text-brand-700">
                              主要
                            </span>
                          ) : null}
                        </div>
                        {meta ? (
                          <p className="break-words text-xs text-slate-500">{meta}</p>
                        ) : null}
                        {reachable ? (
                          <p className="break-words text-xs text-slate-500">{reachable}</p>
                        ) : null}
                        {contact.remark ? (
                          <p className="break-words text-xs text-slate-400">{contact.remark}</p>
                        ) : null}
                      </div>
                      {writable ? (
                        <div className="flex shrink-0 items-center gap-2 text-xs">
                          <button
                            type="button"
                            onClick={() => openEditContact(contact)}
                            className="text-slate-500 hover:text-brand-600"
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setDeleteError(null);
                              setDeleteTarget(contact);
                            }}
                            className="text-slate-400 hover:text-red-600"
                          >
                            删除
                          </button>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>

        {/* 待办区放右栏：它是"下一步做什么"，与联系人同属这个客户的操作信息。
            只列未完成的前 5 条（后端按 due_at 升序），全量在首页按天汇总。 */}
        <section className="min-w-0 space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-slate-900">待办</h2>
            {writable ? (
              <button
                type="button"
                onClick={() => setShowFollowUpForm(true)}
                className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
              >
                + 新建
              </button>
            ) : (
              <span className="shrink-0 text-xs text-slate-500">只读</span>
            )}
          </div>

          {followUpsQuery.isError ? (
            <p className="break-words text-sm text-red-600">
              {errorMessage(followUpsQuery.error, "待办加载失败")}
            </p>
          ) : followUpsQuery.isPending ? (
            <p className="text-sm text-slate-500">加载中…</p>
          ) : (followUpsQuery.data?.items.length ?? 0) === 0 ? (
            <p className="break-words rounded-md bg-slate-50 p-3 text-sm text-slate-500">
              {writable
                ? "没有未完成的待办。用「+ 新建」给自己排一条，到期后会出现在首页。"
                : "该客户没有未完成的待办。"}
            </p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {(followUpsQuery.data?.items ?? []).map((todo) => (
                <li key={todo.id} className="flex items-start justify-between gap-2 py-2">
                  <div className="min-w-0 space-y-0.5">
                    <p className="break-words text-sm text-slate-900">{todo.content}</p>
                    <p
                      className={`break-words text-xs ${
                        isOverdue(todo.due_at) ? "text-red-600" : "text-slate-500"
                      }`}
                    >
                      {formatDay(todo.due_at)}
                      {" · "}
                      {isOverdue(todo.due_at) ? "已逾期" : `${formatClock(todo.due_at)} 前`}
                    </p>
                  </div>
                  {writable ? (
                    <div className="flex shrink-0 items-center gap-2 text-xs">
                      <button
                        type="button"
                        onClick={() => {
                          setDoneError(null);
                          setDoneTarget(todo);
                        }}
                        className="text-slate-500 hover:text-brand-600"
                      >
                        完成
                      </button>
                      <button
                        type="button"
                        disabled={cancelTodo.isPending}
                        onClick={() => {
                          setTodoError(null);
                          cancelTodo.mutate(todo.id);
                        }}
                        className="text-slate-400 hover:text-red-600 disabled:opacity-60"
                      >
                        取消
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          {(followUpsQuery.data?.total ?? 0) > (followUpsQuery.data?.items.length ?? 0) ? (
            <p className="text-xs text-slate-400">
              共 {followUpsQuery.data?.total} 条未完成，这里显示最早到期的 5 条。
            </p>
          ) : null}

          {todoError ? <p className="break-words text-sm text-red-600">{todoError}</p> : null}
        </section>
      </div>

      {/* 机会区放在两栏之外、整行铺满：机会是"这个客户在谈什么"的主线，塞进
          380px 右栏会挤成三列字。可见性继承客户：看不到客户就看不到这里。
          这里只读列表 + 新建入口；改阶段/拖拽属于看板（本页不做第二套交互）。 */}
      <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-900">销售机会</h2>
          <div className="flex items-center gap-2">
            <Link to="/opportunities/board" className="text-sm text-brand-600 hover:underline">
              看板
            </Link>
            {writable ? (
              <button
                type="button"
                onClick={() => setShowOpportunityForm(true)}
                className="rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
              >
                + 新建机会
              </button>
            ) : (
              <span className="text-xs text-slate-500">只读</span>
            )}
          </div>
        </div>

        {opportunitiesQuery.isError ? (
          <p className="break-words text-sm text-red-600">
            {errorMessage(opportunitiesQuery.error, "机会加载失败")}
          </p>
        ) : opportunitiesQuery.isPending ? (
          <p className="text-sm text-slate-500">加载中…</p>
        ) : (opportunitiesQuery.data?.items.length ?? 0) === 0 ? (
          <p className="text-sm text-slate-500">
            还没有机会。{writable ? "点右上角「+ 新建机会」开始记录。" : ""}
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {(opportunitiesQuery.data?.items ?? []).map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm"
              >
                <span className="min-w-0 break-words font-medium text-slate-900">
                  {item.name}
                </span>
                <span
                  className={`inline-flex shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                    STAGE_BADGE_CLASS[item.stage] ?? "bg-slate-100 text-slate-700"
                  }`}
                >
                  {STAGE_LABELS[item.stage] ?? item.stage}
                </span>
                <span className="shrink-0 text-slate-600">{formatAmount(item.amount)}</span>
                <span className="break-words text-xs text-slate-500">
                  预计成交 {formatDateOnly(item.expected_close_date)} · 赢单率 {item.win_rate}% ·
                  负责人 {item.owner_name}
                </span>
                {item.lost_reason_name ? (
                  <span className="break-words text-xs text-slate-500">
                    输单原因：{item.lost_reason_name}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 跟进时间线（spec §6.2）：与机会区同为主线段，整行铺满。
          条目由后端合并 activity 与 stage_change 并倒序，前端不重排。 */}
      <section className="min-w-0 space-y-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-900">跟进时间线</h2>
          {writable ? (
            <button
              type="button"
              onClick={() => {
                setActivityDraft(null);
                setShowActivityForm(true);
              }}
              className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
            >
              + 写跟进
            </button>
          ) : (
            <span className="shrink-0 text-xs text-slate-500">只读</span>
          )}
        </div>
        {/* 时间线自带加载更多与空态；客户不可见时这里根本不会渲染（上方已 return）。
            writable 必须传进去：公海客户的历史跟进对非 admin 只读，行内按钮要收口。 */}
        <Timeline
          customerId={customerId}
          writable={writable}
          onEdit={(draft) => {
            setActivityDraft(draft);
            setShowActivityForm(true);
          }}
        />
      </section>

      {/* 移动端悬浮「+ 跟进」（spec §6.3）：桌面端已有卡片右上角入口，故 lg 以上隐藏。
          bottom-20 避开 iOS 浏览器底栏；z-30 低于弹窗（Modal 是 z-40+）。 */}
      {writable ? (
        <button
          type="button"
          onClick={() => {
            setActivityDraft(null);
            setShowActivityForm(true);
          }}
          className="fixed bottom-20 right-4 z-30 rounded-full bg-brand-600 px-5 py-3 text-sm font-medium text-white shadow-lg lg:hidden"
        >
          + 跟进
        </button>
      ) : null}

      {showActivityForm ? (
        <ActivityFormModal
          customerId={customerId}
          draft={activityDraft ?? undefined}
          onClose={() => {
            setShowActivityForm(false);
            setActivityDraft(null);
          }}
          onSaved={() => {
            setShowActivityForm(false);
            setActivityDraft(null);
          }}
        />
      ) : null}

      {showFollowUpForm ? (
        <FollowUpFormModal
          customerId={customerId}
          contactOptions={contacts}
          opportunityOptions={opportunitiesQuery.data?.items ?? []}
          onClose={() => setShowFollowUpForm(false)}
          onSaved={() => setShowFollowUpForm(false)}
        />
      ) : null}

      {doneTarget ? (
        <FollowUpDoneModal
          followUp={doneTarget}
          pending={completeTodo.isPending}
          error={doneError}
          onClose={() => {
            setDoneTarget(null);
            setDoneError(null);
          }}
          onSubmit={(createActivity, activityType) =>
            completeTodo.mutate({ id: doneTarget.id, createActivity, activityType })
          }
        />
      ) : null}

      {contactModal !== null ? (
        <Modal
          title={contactModal.mode === "create" ? "添加联系人" : "编辑联系人"}
          onClose={() => setContactModal(null)}
        >
          <div className="space-y-3">
            <Field
              label="姓名 *"
              maxLength={64}
              value={contactForm.name}
              error={contactFieldErrors.name}
              onChange={(e) => updateContactForm({ name: e.target.value })}
            />
            <div className="grid grid-cols-2 gap-3">
              <Field
                label="职位"
                maxLength={64}
                value={contactForm.title}
                error={contactFieldErrors.title}
                onChange={(e) => updateContactForm({ title: e.target.value })}
              />
              <Field
                label="手机"
                maxLength={32}
                inputMode="tel"
                value={contactForm.phone}
                error={contactFieldErrors.phone}
                onChange={(e) => updateContactForm({ phone: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field
                label="微信"
                maxLength={64}
                value={contactForm.wechat}
                error={contactFieldErrors.wechat}
                onChange={(e) => updateContactForm({ wechat: e.target.value })}
              />
              <Field
                label="邮箱"
                type="email"
                maxLength={128}
                value={contactForm.email}
                error={contactFieldErrors.email}
                onChange={(e) => updateContactForm({ email: e.target.value })}
              />
            </div>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-700">决策角色</span>
              <select
                value={contactForm.decision_role}
                onChange={(e) => updateContactForm({ decision_role: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">未填</option>
                {DECISION_ROLE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={contactForm.is_primary}
                  onChange={(e) => updateContactForm({ is_primary: e.target.checked })}
                />
                设为主要联系人
              </label>
              <p className="mt-1 text-xs text-slate-500">
                同一客户至多一个主联系人；设为「主要」后，原来的主联系人会自动取消。
              </p>
            </div>
            <TextArea
              label="备注"
              rows={3}
              value={contactForm.remark}
              onChange={(e) => updateContactForm({ remark: e.target.value })}
            />
            {contactError ? (
              <p className="break-words text-sm text-red-600">{contactError}</p>
            ) : null}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setContactModal(null)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                取消
              </button>
              <button
                type="button"
                disabled={!contactForm.name.trim() || saveContact.isPending}
                onClick={submitContact}
                className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
              >
                {saveContact.isPending ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {showOpportunityForm ? (
        <OpportunityFormModal
          mode="create"
          fixedCustomer={{ id: customer.id, name: customer.name }}
          onClose={() => setShowOpportunityForm(false)}
        />
      ) : null}

      {deleteTarget !== null ? (
        <Modal title="删除联系人" onClose={() => setDeleteTarget(null)}>
          <div className="space-y-3">
            <p className="break-words text-sm text-slate-700">
              确定删除联系人「<span className="font-medium">{deleteTarget.name}</span>」？删除后不可恢复。
            </p>
            {deleteTarget.is_primary ? (
              <p className="break-words text-xs text-slate-500">
                它是当前的主联系人，删除后该客户将没有主联系人（不会自动指定新的）。
              </p>
            ) : null}
            {deleteError ? (
              <p className="break-words text-sm text-red-600">{deleteError}</p>
            ) : null}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                取消
              </button>
              <button
                type="button"
                disabled={removeContact.isPending}
                onClick={() => {
                  setDeleteError(null);
                  removeContact.mutate(deleteTarget.id);
                }}
                className="rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-60"
              >
                {removeContact.isPending ? "删除中…" : "删除"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
