import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import { changeStage, getBoard } from "../api/opportunities";
import type { Opportunity } from "../api/types";
import { QueryError } from "../components/QueryError";
import { StageChangeModal } from "../components/StageChangeModal";
import {
  STAGE_BADGE_CLASS,
  STAGE_LABELS,
  formatAmount,
  formatDateOnly,
  isTerminalStage,
} from "../constants/opportunityLabels";

/** Tailwind `md` 断点；与页面上的 `md:block` 之类保持同一个值。 */
const DESKTOP_DRAG_QUERY = "(min-width: 768px)";

/**
 * 只有桌面宽度才启用 HTML5 拖拽。
 *
 * spec §6.3 明确不做触屏拖拽：手指拖动与页面纵向/横向滚动抢同一个手势，
 * 误触率太高——用户想滚动看板，结果把一单生意拖进了输单。手机上改用
 * "横向滚动分列 + 点卡片弹窗选阶段"，同一次改阶段，少一层误操作风险。
 */
function useDesktopDragEnabled(): boolean {
  const [enabled, setEnabled] = useState(() => window.matchMedia(DESKTOP_DRAG_QUERY).matches);

  useEffect(() => {
    const media = window.matchMedia(DESKTOP_DRAG_QUERY);
    const onChange = (event: MediaQueryListEvent) => setEnabled(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return enabled;
}

/**
 * 机会看板（design §6.2 / §6.3）。
 *
 * 数据来自专用端点 `GET /opportunities/board`：每列自带 `total` / `amount_total`
 * 的全量聚合，`items` 只是前 200 条。所以列头一律读 `total`，绝不读
 * `items.length`——200 条以上时后者会把"这一列有多少单"显示成假数字。
 *
 * 拖拽**不做乐观移动**：卡片在服务端确认前留在原列。乐观移动要么多一套回滚状态，
 * 要么在失败后"先跳过去再弹回来"，用户会以为已经成功了；这里失败时卡片根本没动，
 * 只在顶部出一条错误，语义与事实一致。成功后才 invalidate `["board"]` 重新取数
 * （Task 8 的列表页在推进成功后也 invalidate 这个键，两页数据因此始终一致）。
 *
 * 一次移动请求在飞时，**所有卡片与列一起暂停接收拖拽**，顶部同时显示"正在移动到…"。
 * 之所以整体暂停而不是只挡同一张卡：后端对"同一阶段再提交"是 400，但两个**不同**
 * 机会的并发变更虽然都合法，请求回来的顺序却不保证，两次 invalidate 交错时看板会
 * 短暂显示一半新一半旧；何况本页刻意不做乐观移动，卡片必须等服务端确认才换列，
 * 并发的第二张卡在界面上同样"拖了没反应"。整体暂停 + 可见进度条是唯一能让用户
 * 一眼看出"系统正在处理上一次操作"的做法，也只需要一个 `isPending` 真值源。
 * （按 id 记在飞集合要多一份 Set 状态与逐卡 loading 标记，收益只是"能排队的第二
 * 次拖拽"，而本页一次变更通常一秒内就回来。）
 */
export default function OpportunityBoardPage() {
  const queryClient = useQueryClient();
  const dragEnabled = useDesktopDragEnabled();
  const [dragging, setDragging] = useState<Opportunity | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);
  const [modal, setModal] = useState<{ opportunity: Opportunity; stage: string } | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  // 拖拽结束的瞬间浏览器可能仍补发一次 click（不同浏览器行为不一），
  // 不拦的话"拖完立刻弹出一个阶段弹窗"会被当成 bug。
  const dragEndedAt = useRef(0);

  const query = useQuery({ queryKey: ["board"], queryFn: () => getBoard() });

  const move = useMutation({
    // 普通推进：目标是推进阶段，输单字段必须**显式传 null**——后端对
    // "非 lost 却带非空 lost_reason_id / lost_note"直接 400，省略字段虽然也等价，
    // 但显式 null 才把"这次变更与输单无关"写进请求体。
    mutationFn: ({ id, stage }: { id: number; stage: string }) =>
      changeStage(id, { stage, lost_reason_id: null, lost_note: null }),
    onSuccess: async () => {
      // 键必须是 ["board"]：列表页推进成功时 invalidate 的是同一个键。
      await queryClient.invalidateQueries({ queryKey: ["board"] });
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });

  // 请求在飞期间整体冻结拖拽入口（见组件头）。卡片与列都看这个真值源，
  // 不存在"卡片还能拖、列却拒收"这种一半可用的中间态。
  const canDrag = dragEnabled && !move.isPending;

  /**
   * 落点处理：需要额外输入的先弹窗，其余直接提交。
   *
   * 不在这里做任何本地列变更——见组件头的"不做乐观移动"。
   */
  function handleDrop(targetStage: string) {
    const opportunity = dragging;
    setDragging(null);
    setDragOverStage(null);
    // 上一次移动还没回来：绝不再发第二个请求（第二个必然 400「机会已处于该阶段」，
    // 而且会与第一次的 invalidate 抢着刷新看板）。正常路径上它不可达——`canDrag`
    // 已经让卡片与列都拒绝拖拽——留在这里只是兜底，防止将来新增的入口绕过 `canDrag`。
    if (move.isPending) return;
    // 每个新落点都从干净状态开始：上一次请求留下的红字不能遮住这一次的本地拦截文案。
    move.reset();
    setLocalError(null);
    if (opportunity === null) return;
    // 同阶段后端 400，本就不该发请求
    if (opportunity.stage === targetStage) return;

    // 终态之间不能互转（won ↔ lost 后端一律 400）：UI 不提供这条路，
    // 也不弹一个"只有推进阶段可选"的弹窗去暗示它可行。
    if (isTerminalStage(opportunity.stage) && isTerminalStage(targetStage)) {
      setLocalError("已关闭的机会不能直接改成另一种终态，请先重开到某个推进阶段。");
      return;
    }

    // 进赢单会写 `won_at`（之后只能重开），进输单要选原因，从终态重开要填原因：
    // 三种情况都先弹窗，把必填项摆到眼前，而不是让后端的 400 出现在用户脸上。
    if (targetStage === "won" || targetStage === "lost" || isTerminalStage(opportunity.stage)) {
      setModal({ opportunity, stage: targetStage });
      return;
    }

    move.mutate({ id: opportunity.id, stage: targetStage });
  }

  if (query.isLoading) {
    return <p className="text-sm text-slate-500">看板加载中…</p>;
  }
  if (query.isError) {
    return (
      <QueryError
        error={query.error}
        fallback="看板加载失败，请重试"
        onRetry={() => void query.refetch()}
      />
    );
  }

  const columns = query.data?.columns ?? [];
  const allEmpty = columns.length > 0 && columns.every((column) => column.total === 0);
  // 阶段变更的两条失败来源：本次请求被后端拒绝，或被前端提前拦下的非法落点。
  // 本地拦截优先：用户刚做的动作是"拖到一个不允许的列"，只该看到这条解释；
  // 上一次请求的错误已在 handleDrop 起始 reset 掉，这里再取一次优先级是双保险。
  const errorText = localError ?? (move.isError ? errorMessage(move.error, "阶段变更失败，请重试") : null);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-base font-semibold text-slate-900">机会看板</h1>
        <Link
          to="/opportunities"
          className="rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
        >
          列表视图
        </Link>
        <p className="w-full text-xs text-slate-500 sm:w-auto">
          桌面可直接拖动卡片改阶段；手机上点卡片选阶段。落位后由服务端确认，成功后才换列，失败时卡片留在原列。
        </p>
      </div>

      {/* 在飞期间唯一可见的"系统正在处理"信号（配合全局禁用拖拽）。
          role="status" 让读屏用户也听得到，而不只是靠"拖不动"去猜。 */}
      {move.isPending ? (
        <p role="status" className="text-sm text-slate-600">
          正在移动到「{STAGE_LABELS[move.variables?.stage ?? ""] ?? "目标阶段"}」…
        </p>
      ) : null}

      {errorText ? <p className="break-words text-sm text-red-600">{errorText}</p> : null}

      {columns.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
          看板暂无数据，请到列表视图新建机会。
        </p>
      ) : null}
      {allEmpty ? (
        <p className="rounded-lg border border-slate-200 bg-white p-4 text-center text-sm text-slate-500">
          看板暂无机会，可在
          <Link to="/opportunities" className="mx-1 text-brand-600 hover:underline">
            列表视图
          </Link>
          新建。
        </p>
      ) : null}

      {/* 窄屏横向滚动分列（spec §6.3）：不把六列换成"按阶段叠起来的列表"，
          否则"各阶段各有多少单"这个看板唯一的价值在手机上就看不见了。 */}
      <div className="flex gap-3 overflow-x-auto pb-2">
        {columns.map((column) => (
          <section
            key={column.stage}
            onDragOver={(event) => {
              // 请求在飞时不接受放置：不 preventDefault，浏览器就不会把这一列
              // 当成合法落点（否则会出现"高亮了却什么都没发生"）。
              if (!canDrag) return;
              // 不 preventDefault 的话浏览器认为这一列不可放置，drop 根本不会触发
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              setDragOverStage(column.stage);
            }}
            // 不写 onDragLeave：指针从列移到列内某张卡片时浏览器也会发 dragleave，
            // 高亮会闪。dragover 连续触发会把高亮带到新列，dragEnd 负责收尾清空。
            onDrop={(event) => {
              event.preventDefault();
              if (!canDrag) return;
              handleDrop(column.stage);
            }}
            className={`flex min-w-[240px] flex-1 flex-col gap-2 rounded-lg border p-2 ${
              dragOverStage === column.stage
                ? "border-brand-400 bg-brand-50"
                : "border-slate-200 bg-slate-50"
            }`}
          >
            <header className="flex items-center justify-between gap-2 px-1">
              <span
                className={`inline-flex shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                  STAGE_BADGE_CLASS[column.stage] ?? "bg-slate-100 text-slate-700"
                }`}
              >
                {STAGE_LABELS[column.stage] ?? column.stage}
              </span>
              {/* `total` / `amount_total` 是后端对该列**全量**的聚合 */}
              <span className="shrink-0 text-xs text-slate-500">
                {column.total} 条 · {formatAmount(column.amount_total)}
              </span>
            </header>

            {column.items.map((item) => (
              <article
                key={item.id}
                // 窄屏不给 draggable：触屏上"长按拖动"与滚动抢手势（见 hook 注释）。
                // 在飞期间也关掉：与其让用户拖完发现被静默忽略，不如一开始就拖不动，
                // 顶部同时有"正在移动…"，"不能拖"是有解释的。
                draggable={canDrag}
                onDragStart={(event) => {
                  // 写一个 text/plain 只是为了让 Firefox 认这次拖拽；真正的数据在 state 里
                  event.dataTransfer.setData("text/plain", String(item.id));
                  event.dataTransfer.effectAllowed = "move";
                  setDragging(item);
                }}
                onDragEnd={() => {
                  dragEndedAt.current = Date.now();
                  setDragging(null);
                  setDragOverStage(null);
                }}
                onClick={() => {
                  // 300ms 内刚结束一次拖拽：这次 click 是拖拽的副产物，不弹窗
                  if (Date.now() - dragEndedAt.current < 300) return;
                  // 打开弹窗前清掉看板上的旧错误，否则上一次请求的红字会留在页面上，
                  // 与弹窗里这一次的输入毫无关系。**在飞时不 reset**：reset 会把
                  // `isPending` 清成 false，正好放行一次本不该并发的拖拽。
                  if (!move.isPending) move.reset();
                  setLocalError(null);
                  setModal({ opportunity: item, stage: item.stage });
                }}
                className={`cursor-pointer space-y-1 rounded-md border border-slate-200 bg-white p-2 text-sm shadow-sm hover:border-brand-300 ${
                  dragging?.id === item.id ? "opacity-50" : ""
                }`}
              >
                <p className="break-words font-medium text-slate-900">{item.name}</p>
                <p className="break-words text-xs text-slate-500">{item.customer_name}</p>
                {/* 金额只格式化，不做数值运算；日期列才用 formatDateOnly */}
                <p className="text-xs text-slate-600">
                  {formatAmount(item.amount)} · {formatDateOnly(item.expected_close_date)}
                </p>
                <p className="text-xs text-slate-400">
                  {item.owner_name} · {item.win_rate}%
                  {item.win_rate_manual ? "（手）" : ""}
                </p>
              </article>
            ))}

            {/* 空列留一块看得见、接得住拖拽的落点，而不是一片空白让人以为不能放 */}
            {column.items.length === 0 ? (
              <p className="flex flex-1 items-center justify-center rounded-md border border-dashed border-slate-300 px-2 py-6 text-center text-xs text-slate-400">
                {canDrag ? "暂无机会，可拖到这里" : "暂无机会"}
              </p>
            ) : null}

            {/* `items` 只给前 200 条（后端 BOARD_COLUMN_LIMIT）：超出时必须说清楚，
                否则用户会以为这一列就只有这么多单。 */}
            {column.total > column.items.length ? (
              <p className="px-1 text-xs text-slate-400">
                仅显示前 {column.items.length} 条，共 {column.total} 条
              </p>
            ) : null}
          </section>
        ))}
      </div>

      {modal ? (
        <StageChangeModal
          opportunity={modal.opportunity}
          initialStage={modal.stage}
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}
