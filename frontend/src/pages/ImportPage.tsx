import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  commitImport,
  downloadCustomerTemplate,
  uploadCustomers,
} from "../api/imports";
import type { ImportJob } from "../api/types";
import { useAuth } from "../auth/AuthProvider";

const STATUS_LABELS: Record<ImportJob["status"], string> = {
  validating: "校验中",
  validated: "校验通过，待确认",
  importing: "写入中",
  done: "已完成",
  failed: "校验未通过",
};

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // 立刻 revoke 在部分浏览器会取消下载；让出当前任务再释放
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportErrors(job: ImportJob) {
  const header = "行号,列,原因";
  const lines = job.errors.map((item) =>
    [item.row, item.column, item.message]
      .map((value) => `"${String(value).replace(/"/g, '""')}"`)
      .join(","),
  );
  // BOM：Excel 直接打开 CSV 不乱码
  const blob = new Blob(["\uFEFF" + [header, ...lines].join("\n")], {
    type: "text/csv;charset=utf-8",
  });
  downloadBlob(blob, `import-${job.id}-errors.csv`);
}

export default function ImportPage() {
  const { user } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);

  const templateMutation = useMutation({
    mutationFn: downloadCustomerTemplate,
    onSuccess: (blob) => downloadBlob(blob, "customers-template.xlsx"),
  });

  const uploadMutation = useMutation({
    mutationFn: uploadCustomers,
    onSuccess: (result) => setJob(result),
  });

  const commitMutation = useMutation({
    mutationFn: commitImport,
    onSuccess: (result) => setJob(result),
  });

  const error = uploadMutation.error ?? commitMutation.error ?? templateMutation.error;
  const busy = uploadMutation.isPending || commitMutation.isPending;

  // 页面级闸门：导航已按 `managerOrAdmin` 隐藏，但手输 URL 仍能直达。
  // 放在所有 hook 之后，保证 hook 顺序不随角色变化。
  if (user?.role === "sales") return <Navigate to="/" replace />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-900">客户导入</h1>
        <button
          type="button"
          onClick={() => templateMutation.mutate()}
          disabled={templateMutation.isPending}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          下载模板
        </button>
      </div>

      <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-3">
        <p className="text-sm text-slate-600">
          只支持 `.xlsx`，单次最多 5000 行、5MB。重名客户会跳过，不会覆盖；
          负责人必须填系统登录名，且销售主管只能导入本部门负责人。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="file"
            accept=".xlsx"
            onChange={(event) => {
              setJob(null);
              setFile(event.target.files?.[0] ?? null);
            }}
            className="max-w-full text-sm"
          />
          <button
            type="button"
            disabled={!file || busy}
            onClick={() => file && uploadMutation.mutate(file)}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {uploadMutation.isPending ? "上传校验中…" : "上传并校验"}
          </button>
        </div>
        {error ? (
          <p className="text-sm text-red-600">{errorMessage(error, "操作失败")}</p>
        ) : null}
      </section>

      {job ? (
        <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-medium text-slate-900">{job.file_name}</p>
              <p className="text-xs text-slate-500">
                {STATUS_LABELS[job.status]} · 共 {job.total_rows} 行 · 成功{" "}
                {job.success_rows} · 跳过 {job.skipped_rows} · 错误 {job.error_rows}
              </p>
            </div>
            <div className="flex gap-2">
              {job.errors.length > 0 ? (
                <button
                  type="button"
                  onClick={() => exportErrors(job)}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
                >
                  导出错误 CSV
                </button>
              ) : null}
              {job.status === "validated" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => commitMutation.mutate(job.id)}
                  className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
                >
                  {commitMutation.isPending ? "写入中…" : "确认写入"}
                </button>
              ) : null}
            </div>
          </div>

          {job.status === "done" ? (
            <p className="text-sm text-emerald-700">
              已写入 {job.success_rows} 个客户，跳过 {job.skipped_rows} 个重名。
            </p>
          ) : null}

          {job.errors.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    <th className="px-2 py-1.5">行号</th>
                    <th className="px-2 py-1.5">列</th>
                    <th className="px-2 py-1.5">原因</th>
                  </tr>
                </thead>
                <tbody>
                  {job.errors.map((item, index) => (
                    <tr key={index} className="border-b border-slate-100 last:border-0">
                      <td className="px-2 py-1.5 text-slate-700">{item.row}</td>
                      <td className="px-2 py-1.5 text-slate-700">{item.column || "—"}</td>
                      <td className="px-2 py-1.5 text-slate-700">{item.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}