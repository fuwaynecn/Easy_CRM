import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, errorMessage } from "../api/client";
import { createCustomer, listCustomers } from "../api/customers";
import { Field, TextArea } from "../components/Field";
import { Modal } from "../components/Modal";

const LEVEL_LABEL: Record<string, string> = { A: "A 级", B: "B 级", C: "C 级" };
const BUDGET_LABEL: Record<string, string> = {
  approved: "已立项",
  pending: "预算未批",
  none: "无预算",
  unknown: "未知",
};
const PROCUREMENT_LABEL: Record<string, string> = {
  tender: "招标",
  direct: "直采",
  framework: "框架协议",
  other: "其他",
};

const EMPTY_FORM = {
  name: "",
  industry: "",
  region: "",
  level: "C",
  procurement_type: "",
  budget_status: "unknown",
  source: "",
  remark: "",
};

export default function CustomerListPage() {
  const queryClient = useQueryClient();
  const [keyword, setKeyword] = useState("");
  const [level, setLevel] = useState("");
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState<{ id: number; name: string }[]>([]);

  const query = useQuery({
    queryKey: ["customers", keyword, level, page],
    queryFn: () => listCustomers({ keyword, level, page, page_size: 20 }),
    // 翻页/改筛选时先沿用上一页数据，避免列表整片清空导致跳版；首次加载不受影响。
    placeholderData: keepPreviousData,
  });

  const createMutation = useMutation({
    mutationFn: (force: boolean) =>
      createCustomer({
        name: form.name,
        industry: form.industry || undefined,
        region: form.region || undefined,
        level: form.level,
        procurement_type: form.procurement_type || undefined,
        budget_status: form.budget_status,
        source: form.source || undefined,
        remark: form.remark || undefined,
        force,
      }),
    // 上一次提交的报错不能留到这一次：用户分不清是本次结果还是陈的。
    onMutate: () => {
      setFormError(null);
    },
    onSuccess: async () => {
      setShowCreate(false);
      setDuplicates([]);
      setForm(EMPTY_FORM);
      // 带着关键字停在第 3 页时，新建的客户根本不在视野里，用户会以为没建成。
      // 回到默认视图（全部 / 第 1 页）再刷新。
      setKeyword("");
      setLevel("");
      setPage(1);
      await queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as { similar?: { id: number; name: string }[] } | undefined;
        setDuplicates(detail?.similar ?? []);
        setFormError(errorMessage(err, "客户名称疑似重复"));
        return;
      }
      setFormError(errorMessage(err, "创建失败"));
    },
  });

  const data = query.data;
  // 页码一律以服务端回显为准（后端可能把越界页钳制回最后一页）。
  const currentPage = data?.page ?? page;
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / Math.max(1, data?.page_size ?? 20)));
  // 403 是"看得到记录但没权限"，必须只显示提示、不渲染任何客户字段。
  const forbidden = query.error instanceof ApiError && query.error.status === 403;

  // 每次打开弹窗都从干净状态开始：否则上一次的报错/重复提示会跟着新表单进来，
  // 按钮还显示「确认仍要创建」，而那是针对上一次名称的授权。
  function openCreate() {
    setForm(EMPTY_FORM);
    setDuplicates([]);
    setFormError(null);
    setShowCreate(true);
  }

  // 越界回落：别人删掉本页最后一条、或本地 page 与服务端回显不一致时，
  // 跳到仍在范围里的页码，避免「暂无客户 + 有效页码」同时出现。
  // 占位数据（keepPreviousData）是上一组查询的结果，不能拿它来同步页码。
  useEffect(() => {
    if (!data || query.isPlaceholderData) return;
    if (data.page !== page) {
      setPage(data.page);
      return;
    }
    if (data.items.length === 0 && page > 1) setPage(pageCount);
  }, [data, page, pageCount, query.isPlaceholderData]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="搜索客户"
          value={keyword}
          onChange={(e) => {
            setPage(1);
            setKeyword(e.target.value);
          }}
          placeholder="搜索客户名称 / 简称 / 编号"
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm sm:w-64"
        />
        <select
          aria-label="按等级筛选"
          value={level}
          onChange={(e) => {
            setPage(1);
            setLevel(e.target.value);
          }}
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">全部等级</option>
          <option value="A">A 级</option>
          <option value="B">B 级</option>
          <option value="C">C 级</option>
        </select>
        <button
          type="button"
          onClick={openCreate}
          className="ml-auto rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600"
        >
          + 新建客户
        </button>
      </div>

      {query.isLoading ? <p className="text-sm text-slate-500">加载中…</p> : null}

      {query.isError ? (
        // 403 不登出、不跳登录页，只在本页给中文提示；也不给重试（重试同样的
        // 越权请求只会再失败一次）。
        <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <p className="break-words text-red-600">
            {forbidden
              ? errorMessage(query.error, "无权查看该客户")
              : errorMessage(query.error, "加载失败，请重试")}
          </p>
          {forbidden ? null : (
            <button
              type="button"
              onClick={() => void query.refetch()}
              className="mt-2 rounded border border-slate-300 px-2 py-1"
            >
              重试
            </button>
          )}
        </div>
      ) : null}

      {/* 出错时连占位数据一起藏起来：状态切换期间不能把上一个查询的客户字段留在屏幕上。 */}
      {query.isError ? null : (
        <>
          <div className="hidden overflow-hidden rounded-lg border border-slate-200 bg-white md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">客户名称</th>
                  <th className="px-3 py-2 font-medium">等级</th>
                  <th className="px-3 py-2 font-medium">地区</th>
                  <th className="px-3 py-2 font-medium">采购方式</th>
                  <th className="px-3 py-2 font-medium">预算</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((c) => (
                  <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-3 py-2">
                      <Link
                        to={`/customers/${c.id}`}
                        className="break-words text-brand-600 hover:underline"
                      >
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{LEVEL_LABEL[c.level] ?? c.level}</td>
                    <td className="px-3 py-2 text-slate-600">{c.region ?? "—"}</td>
                    <td className="px-3 py-2 text-slate-600">
                      {c.procurement_type
                        ? (PROCUREMENT_LABEL[c.procurement_type] ?? c.procurement_type)
                        : "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      {BUDGET_LABEL[c.budget_status] ?? c.budget_status}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="space-y-2 md:hidden">
            {data?.items.map((c) => (
              <Link
                key={c.id}
                to={`/customers/${c.id}`}
                className="block break-words rounded-lg border border-slate-200 bg-white p-3"
              >
                {/* min-w-0 + break-words：不含空格的长拉丁串（英文公司名、误粘贴的
                    URL/编号）可以在卡片内断行，不会把 375px 的视口撑出横向滚动条。 */}
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="min-w-0 break-words font-medium text-slate-900">{c.name}</span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {LEVEL_LABEL[c.level] ?? c.level}
                  </span>
                </div>
                <div className="mt-1 break-words text-xs text-slate-500">
                  {c.region ?? "—"} · {BUDGET_LABEL[c.budget_status] ?? c.budget_status}
                </div>
              </Link>
            ))}
          </div>

          {/* 空态放在桌面/移动两条分支之外：md 以下桌面容器是 display:none，
              空态若留在里面，移动端搜不到结果时会看到整页空白。 */}
          {data && data.items.length === 0 ? (
            <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
              暂无客户
            </p>
          ) : null}

          {data && data.total > data.page_size ? (
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
              <span>
                共 {data.total} 条，第 {currentPage} / {pageCount} 页
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={currentPage <= 1}
                  onClick={() => setPage(Math.max(1, currentPage - 1))}
                  className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={currentPage >= pageCount}
                  onClick={() => setPage(currentPage + 1)}
                  className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                >
                  下一页
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}

      {showCreate ? (
        <Modal title="新建客户" onClose={() => setShowCreate(false)}>
          <div className="space-y-3">
            <Field
              label="客户名称 *"
              value={form.name}
              // 名字一改，之前的"可能重复"提示就不再指向这次要提交的对象；
              // 不清空会带着 force:true 跳过对「新名称」的查重。
              onChange={(e) => {
                setForm({ ...form, name: e.target.value });
                setDuplicates([]);
                setFormError(null);
              }}
            />
            <div className="grid grid-cols-2 gap-3">
              <Field
                label="行业"
                value={form.industry}
                onChange={(e) => setForm({ ...form, industry: e.target.value })}
              />
              <Field
                label="地区"
                value={form.region}
                onChange={(e) => setForm({ ...form, region: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-slate-700">等级</span>
                <select
                  value={form.level}
                  onChange={(e) => setForm({ ...form, level: e.target.value })}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value="A">A</option>
                  <option value="B">B</option>
                  <option value="C">C</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-slate-700">采购方式</span>
                <select
                  value={form.procurement_type}
                  onChange={(e) => setForm({ ...form, procurement_type: e.target.value })}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value="">未填</option>
                  <option value="tender">招标</option>
                  <option value="direct">直采</option>
                  <option value="framework">框架协议</option>
                  <option value="other">其他</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-slate-700">预算</span>
                <select
                  value={form.budget_status}
                  onChange={(e) => setForm({ ...form, budget_status: e.target.value })}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                >
                  <option value="approved">已立项</option>
                  <option value="pending">预算未批</option>
                  <option value="none">无预算</option>
                  <option value="unknown">未知</option>
                </select>
              </label>
            </div>
            <Field
              label="来源"
              value={form.source}
              onChange={(e) => setForm({ ...form, source: e.target.value })}
            />
            <TextArea
              label="备注"
              rows={3}
              value={form.remark}
              onChange={(e) => setForm({ ...form, remark: e.target.value })}
            />
            {formError ? <p className="break-words text-sm text-red-600">{formError}</p> : null}
            {duplicates.length > 0 ? (
              <div className="break-words rounded-md bg-amber-50 p-3 text-sm text-amber-800">
                <p className="font-medium">以下客户可能重复：</p>
                <ul className="mt-1 list-disc pl-5">
                  {duplicates.map((d) => (
                    <li key={d.id} className="break-words">
                      {d.name}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                取消
              </button>
              <button
                type="button"
                disabled={!form.name.trim() || createMutation.isPending}
                onClick={() => createMutation.mutate(duplicates.length > 0)}
                className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
              >
                {duplicates.length > 0 ? "确认仍要创建" : "保存"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
