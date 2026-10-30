# P3 验收清单（跟进记录 / 待办 / 首页 / 时间线）

依据：`docs/superpowers/specs/2026-10-29-easy-crm-design.md` 的 §3.3（customers 的
`last_activity_at` 维护）、§3.8（activities）、§3.9（follow_ups）、§4.5（跟进与待办）、
§5（接口约定）、§6.1（首页与详情页）、§6.3（移动端），以及
`docs/superpowers/plans/2026-10-31-easy-crm-p3.md` 的 Task 1–11 与「P3 裁定」1–16 条。

**pytest 全绿 ≠ 验收通过**：浏览器渲染、375px、文案逐字、动效这类只有人眼能判的，全在 §4；
前端没有测试框架（spec §10/§12 的决定），所以 §4 不是"补充"，是唯一证据。

---

## 1. 本机环境速查

| 组件 | 值 | 本机特有的坑 |
|---|---|---|
| PostgreSQL | 便携版在 `.tmp\pg`，端口 **55432**，trust 认证 | 机器重启后要重跑 `.tmp\pg\start-pg.ps1`；没有 Docker |
| 开发库 | `easycrm`（种子数据） | `easycrm_test` 由 pytest 建/删，验收不要动它 |
| 后端 | **8081**（P3） | **8081 上若还是 P2 旧进程，`/api/v1/home` 会返回 404**。P3 新增的路径不止这一个，看到 404 先查端口/进程，不是缺陷 |
| 端口 8000 | 本机被系统禁用 | 绑定必失败（`WinError 10013`），不要用 8000 |
| 前端 | `localhost:5173`（Vite） | 代理目标默认 8000，本机必须用 `VITE_API_PROXY` 覆盖成 8081 |

**10 秒判别当前 8081 是不是 P3 进程**（未登录访问新端点）：

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8081/api/v1/home -SkipHttpErrorCheck |
  Select-Object -ExpandProperty StatusCode
```

预期 `401`（P3 认出了端点，只是没登录）。若是 `404`，这个端口上是旧实例。

---

## 2. 前置条件（按顺序）

### 2.1 数据库

```powershell
cd E:\Wayne\Doc\CY_CRM
powershell -ExecutionPolicy Bypass -File .tmp\pg\start-pg.ps1
```

### 2.2 迁移到 head + 灌种子（**必须做**）

```powershell
cd E:\Wayne\Doc\CY_CRM\backend
$env:TEMP="E:\Wayne\Doc\CY_CRM\.tmp\w2"; $env:TMP=$env:TEMP
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m scripts.seed
```

P3 比 P2 多两张表（`activities`、`follow_ups`），**漏跑迁移的表现是所有 P3 接口 500
（`relation "follow_ups" does not exist`），不是 404**——本机 2026-10-31 就踩过一次。

种子幂等：紧接着再跑一次 `scripts.seed`，第二行应是 `本次新建 0 条记录：`。

### 2.3 启动 **P3** 后端（8081）

```powershell
cd E:\Wayne\Doc\CY_CRM\backend
$env:TEMP="E:\Wayne\Doc\CY_CRM\.tmp\w2"; $env:TMP=$env:TEMP
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

验证：`/health` → `{"status":"ok"}`；§1 的判别命令 → `401`。

### 2.4 启动前端（代理到 8081）

```powershell
cd E:\Wayne\Doc\CY_CRM\frontend
$env:VITE_API_PROXY="http://127.0.0.1:8081"
npm.cmd run dev
```

打开 <http://localhost:5173> → 登录页（`/` 现在是首页，未登录会被送回 `/login`）。

### 2.5 账号

口令统一 `Passw0rd!`（`backend/scripts/seed.py`）。

| 账号 | 姓名 | 角色 | 部门 | P3 关注点 |
|---|---|---|---|---|
| `admin` | 系统管理员 | 管理员 | 无 | 首页也是"我名下客户"（裁定 3 字面），所以**首页三块为空是预期** |
| `boss` | 李建国 | 主管 | 华东 | 时间线看得到本部门客户；行内无编辑/删除（已知限制） |
| `sales1` | 张明 | 销售 | 华东 | 杭州智造（名下）+ 苏州新能源（公海只读） |
| `sales2` | 王丽 | 销售 | 华南 | 广州鼎盛、宁波海天 |
| `boss_south` | 陈晓峰 | 主管 | 华南 | —— |

**种子库里没有任何 P3 数据**（没有 activity、没有 follow_up），所以首页三块初始为空、
客户详情的时间线初始为「还没有跟进记录」。§4 的第 1 步就是造第一条数据。

---

## 3. 自动化证据

每条格式：**spec 条款 / 实现位置 / 证据（测试名或命令）/ 结论**。
「结论」列写的是**该测试实际断言的东西**，不是"看起来没问题"。

| spec 条款 | 实现位置 | 证据 | 结论 |
|---|---|---|---|
| §3.3 `last_activity_at` 只在新建 activity 时更新，编辑/删除不回算（裁定 8） | `backend/app/modules/activities/service.py` | `test_activities.py::test_create_sets_last_activity_at`、`::test_update_activity_never_touches_last_activity_at`、`::test_delete_activity_never_touches_last_activity_at`、`::test_backdated_activity_does_not_roll_back_last_activity_at` | 三条维护点与"不碰"都有断言；补写一条更早时间的跟进不会把客户时钟往回拨 |
| §3.3 公海客户不可写跟进 | `activities/service.py`、`scope.py` | `test_activities.py::test_create_activity_on_pool_customer_is_403_for_sales`、`test_activities_api.py::test_pool_customer_activity_is_403` | 403 `forbidden`，且库里 activity 数量不变（两层都钉了：服务层 + HTTP 层） |
| §3.8 activity 字段与类型枚举 | `activities/models.py`、`schemas.py`、迁移 `3199fb8aa5f9` | `test_activities.py::test_activity_type_tuple_matches_spec`、`::test_activity_table_is_registered_in_metadata`、`test_migrations.py::test_migrated_schema_keeps_named_checks_and_column_defaults` | 六个 `type` 值由命名 CHECK 钉住；裸 INSERT 非法值报 IntegrityError |
| §3.8 关联对象必须是本客户且可见 | `activities/service.py` | `test_activities.py::test_create_activity_rejects_contact_from_another_customer`、`::test_create_activity_rejects_opportunity_from_another_customer`、`::test_child_links_soft_deleted_is_not_found` | 400/404；跨客户关联与"关联到已软删对象"都被拒 |
| §3.9 待办表结构、索引、`status` 三态 | `followups/models.py`、迁移 `aca0bc139888` | `test_follow_ups.py::test_follow_up_table_is_registered_with_spec_columns`、`::test_follow_up_statuses_match_spec`、`test_migrations.py::test_migrations_match_models` | 列与命名 CHECK 逐项对齐 spec §3.9；两条索引（`(status,due_at)`、`(customer_id,status,due_at)`）由模型声明、经 `compare_metadata` 与迁移产物比对（无差异） |
| §3.9 待办跟着客户走（「我的待办」= 客户负责人） | `followups/service.py::list_follow_ups` | `test_follow_ups.py::test_mine_filter_is_customer_owner_not_created_by`、`::test_creator_can_edit_todo_on_others_customer`、`test_home.py::test_only_my_todos` | 接手人能看到前任留的待办；`created_by` 不是过滤条件 |
| §4.5 写跟进 + 可选下次跟进同事务建待办 | `activities/service.py::create_activity` | `test_activities.py::test_create_activity_with_next_follow_up_creates_pending_todo`、`::test_failed_next_follow_up_rolls_back_everything`、`test_activities_api.py::test_create_activity_with_next_follow_up_creates_todo`、`test_e2e.py::test_p3_follow_up_loop` | 201 之后有且仅有 1 条 pending，内容/到期与请求一致；待办写入失败时跟进一起回滚 |
| §4.5 完成待办可"顺便写条跟进"，并刷新 `last_activity_at` | `followups/service.py::complete_follow_up` | `test_follow_ups.py::test_complete_with_activity_copies_content_and_bumps_last_activity`、`::test_complete_without_ticking_but_passing_type_is_400`、`test_follow_ups_api.py::test_done_returns_follow_up_and_activity`、`test_e2e.py::test_p3_follow_up_loop` | 返回 `{follow_up: done, activity: 同内容}`；未勾选却传 `activity_type` 是 400；客户 `last_activity_at` 前移 |
| §4.5 完成只能走 `/done`（裁定 10） | `followups/router.py`、`schemas.py` | `test_follow_ups.py::test_patch_status_done_is_rejected`、`::test_complete_twice_is_400`、`test_follow_ups_api.py::test_create_list_patch_cancel` | PATCH `status="done"` → 400；重复完成 → 400（不会重复写跟进） |
| §4.5 逾期定义 `due_at < 今天0点(Asia/Shanghai)` 且 pending | `home/service.py::today_bounds` | `test_home.py::test_today_and_overdue_split_by_shanghai_midnight`、`::test_utc_day_boundary_is_not_used`、`::test_day_bounds_are_half_open` | 用上海零点切分而非 UTC；日界半开 `[start, end)` |
| §4.5 完成/取消态冻结（不可再改内容） | `followups/service.py` | `test_follow_ups.py::test_completed_follow_up_cannot_change_status`、`::test_completed_follow_up_cannot_change_content_or_due_at`、`::test_cancelled_follow_up_cannot_change_content_but_can_reopen`、`::test_patch_cancelled_then_reopen` | 已完成行内容/到期/状态都改不动；已取消行只能"重开"，不能直接改成 done |
| §5 错误统一 `{code, message, detail}` | `app/main.py` 异常处理器 | `test_errors.py`（6 条）、`test_activities_api.py::test_pagination_bounds_are_rejected` | 400/401/403/404/422 五种都有 envelope；分页越界是 422 而不是 500 |
| §5 分页 `{items,total,page,page_size}`、`page_size ≤ 200` | `activities/router.py`、`followups/router.py`（`PageParams`） | `test_activities_api.py::test_pagination_bounds_are_rejected`、`test_follow_ups_api.py::test_create_list_patch_cancel` | `page_size=201`/`page=0` → 422 `validation_error` |
| §5 时间一律带偏移的 ISO | `schemas.py`（`datetime` 直出） | `test_activities_api.py::test_create_and_list_activity_through_http`（`fromisoformat` + tzinfo 断言） | 线上形如 `2026-10-31T09:00:00+08:00`；前端必须 `new Date()` 解析 |
| §5 时间线合并 activities + stage_histories 并倒序，游标 `(occurred_at,type,id)` | `timeline/service.py`、`timeline/cursor` 逻辑在同文件 | `test_timeline.py::test_timeline_merges_activities_and_stage_changes_desc`、`::test_timeline_cursor_pages_without_gap_or_duplicate`、`::test_timeline_cursor_roundtrip_and_invalid_cursor` | 合并排序正确；翻页不重不漏；非法游标 400（不是 500） |
| §5 时间线对已删/停用对象照常显示并标注（裁定 1） | `timeline/service.py`、`timeline/schemas.py` | `test_timeline.py::test_timeline_shows_stage_changes_of_deleted_opportunity`、`::test_timeline_marks_deleted_contact_and_disabled_operator` | `opportunity_deleted`/`contact_deleted`/`operator_disabled` 三个布尔为真，条目仍在 |
| §5 时间线金额取快照（裁定 4） | `timeline/service.py` | `test_timeline.py::test_timeline_uses_amount_snapshot_not_current_amount` | 改机会现价后历史行金额不变 |
| §5 时间线不返回已软删的 activity | `timeline/service.py` | `test_timeline.py::test_timeline_hides_soft_deleted_activity` | 删掉的跟进不再出现在时间线（与"已删机会的 stage_change 仍显示"是两回事） |
| §5 `owner_change` 只预留、游标拒绝（裁定 6/14） | `timeline/service.py` | `test_timeline.py::test_timeline_cursor_rejects_unimplemented_owner_change_type` | 手工构造该类型游标 → 400 |
| §6.1 `/` 是首页三块 | `frontend/src/pages/HomePage.tsx`、`App.tsx` | `npm.cmd run build` + §4 第 1–7 条 | 构建过类型检查；渲染只能手测 |
| §6.1 首页「今日待办 / 已逾期 / 即将进公海」口径（裁定 2/13） | `home/service.py`、`pools/rules.py` | `test_home.py::test_today_and_overdue_split_by_shanghai_midnight`、`::test_pool_notice_uses_27_day_threshold`、`::test_pool_notice_excludes_customers_with_open_deal`、`::test_pool_notice_excludes_customers_with_pending_todo`、`::test_pool_notice_excludes_pool_inactive_and_never_followed` | 27 天瞬时阈值 + 两条豁免 + "从未跟进不预警"逐条钉住 |
| §6.1 首页作用域 = 我名下客户 | `home/service.py` | `test_home.py::test_only_my_todos`、`::test_pool_notice_is_scoped_to_visible_customers` | admin 也只看"我名下"（裁定 3 的字面口径） |
| §6.1 每块最多 50 条但 `total` 是全量 | `home/service.py`（`HOME_BLOCK_LIMIT`） | `test_home.py::test_home_block_limit_truncates` | `items` 截断为 50，`total` 仍是真实总数 |
| §6.3 移动端悬浮「+ 跟进」 | `CustomerDetailPage.tsx`（`lg:hidden` + `bottom-20`） | `npm.cmd run build` + §4 第 13–14 条 | 只能手测（无前端测试框架） |
| 前端契约层（类型/日期/金额） | `api/types.ts`、`constants/datetime.ts`、`api/{activities,followUps,home,timeline}.ts` | `npm.cmd run build`（`tsc --noEmit`） | 109 modules 通过；时间格式统一 `hourCycle:"h23"` |
| 迁移与模型一致 | `backend/migrations/versions/*` | `test_migrations.py::test_migrations_match_models`（`compare_metadata` 差异为 `[]`） | 无差异；`alembic heads` 单头 `aca0bc139888` |

配套的横向证据（本文件写作时实测，原始输出见交接文档 §4）：

- `pytest tests -q` → **541 passed**（P3 新增 120 条：`test_activities.py` 38 +
  `test_activities_api.py` 10、`test_follow_ups.py` 38 + `test_follow_ups_api.py` 8、
  `test_timeline.py` 11、`test_home.py` 14、`test_e2e.py` +1；算术自检 421 + 120 = 541）
- `ruff check backend` → `All checks passed!`
- `alembic heads` → 单头 `aca0bc139888`
- `npm.cmd run build` → `109 modules`，`dist/index-*.js 281.33 kB`

---

## 4. 手工验收（前端，20 条）

执行约定：**前一步会改后一步的数据**。凡标〔相对〕的只核对"变化量/是否出现"，不核对绝对数字。
想回到干净种子：按 §6 的 A.3 重建开发库（`seed.py` 幂等但**不覆盖**已改动数据）。

### A. 首页三块（§6.1）

**A1 首页是三块，不是客户列表**
- 账号：`sales1`，直接打开 <http://localhost:5173/>。
- 预期：落在**首页**，导航「首页」高亮；三张卡片：今日待办 / 已逾期 / 即将进公海。
- 预期：种子里没有 P3 数据 → 三块都是空态文案：「今天没有待办。」「没有逾期的待办，保持住。」「暂无客户进入预警区间。」
- 预期：「即将进公海」卡底部有一行灰字：`30 天无跟进进入回收区间，提前 3 天预警；有在谈机会或未完成待办的客户不预警。规则将于 P4 可配置。`
- 结论：☐ 通过　☐ 不通过（备注：____________）

**A2 造一条今日待办后首页立刻有它**
- 账号：`sales1` → 客户 → 杭州智造装备有限公司 → 右下角「+ 写跟进」（宽屏在「跟进时间线」卡右上角）。
- 操作：跟进方式选「拜访」，内容填 `现场确认交期`，底部「下次跟进」填**今天某个时刻** + 内容 `寄样册`，保存。
- 预期：回到首页，该条出现在「今日待办」，右侧显示 `完成`；时间显示为 `HH:mm 前`。
- 预期：该客户不在「即将进公海」（刚写过跟进）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**A3 完成待办 = 写一条跟进 + 从首页消失**
- 操作：首页 A2 那条点「完成」→ 弹窗默认**勾选**「顺便写一条跟进记录」，出现跟进方式下拉 → 选「拜访」→ 提交。
- 预期：该条从「今日待办」消失，「已逾期」也没有它。
- 预期：打开该客户详情 → 时间线**顶部**多一条「拜访 · 寄样册」（完成待办复制的内容）。
- 操作二：再完成一条并**取消勾选**。预期：待办消失，但时间线**不**新增。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**A4 已过点但仍是今天 → 留在今日待办（裁定：不自动进逾期）**
- 操作：造一条到期时间为**今天早些时候**（例如现在 14:00，填 09:00）的待办。
- 预期：它出现在「今日待办」（不是「已逾期」），详情页待办卡里这条时间标红并显示「已逾期」。
- 说明：这是**已知限制**（§5 #5），不是缺陷。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**A5 昨天的待办进「已逾期」**
- 操作：造一条到期在**昨天**的待办。
- 预期：出现在「已逾期」，文案 `已逾期`（不是 `HH:mm 前`）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**A6 「即将进公海」的判定与豁免**〔相对，需手工改库或等 27 天，可跳过〕
- 操作（可选，需 psql）：把某客户 `last_activity_at` 改到 28 天前：
  `UPDATE customers SET last_activity_at = now() - interval '28 days' WHERE name = '杭州智造装备有限公司';`
- 预期：首页出现该客户，`负责人 张明 · 已 28 天无跟进` + 琥珀色 `即将进公海`。
- 预期：给它加一条 pending 待办后**从预警消失**（有未完成待办豁免）；给它建一条没关闭的机会也会消失。
- 恢复：把 `last_activity_at` 改回 `now()`。
- 结论：☐ 通过　☐ 不通过　☐ 未验（数据构造成本高，自动化已覆盖）

### B. 客户详情页（§6.1 右栏 + 时间线）

**B1 三块信息齐全、待办卡位置正确**
- 账号：`sales1` → 杭州智造装备有限公司。
- 预期：左栏客户字段；右栏**联系人卡在上、待办卡在下**；两栏之下是「销售机会」与「跟进时间线」。
- 预期：待办卡只列未完成（A2/A3 之后应为空或只剩新造的），右上角有「+ 新建」，没有已完成的条目。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**B2 待办卡「+ 新建」/「取消」**
- 操作：待办卡「+ 新建」→ 默认时间应是**明天 09:00** → 填内容保存。
- 预期：卡片**立刻**出现该条（不是等刷新），首页同步出现（若到期是今天）。
- 操作：点「取消」。预期：该条从卡片消失；刷新后仍不在（状态是 cancelled，不是物理删除）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**B3 时间线条目与分页**
- 操作：给该客户写够 21 条以上跟进（附录 A.1），回详情页看时间线。
- 预期：倒序（最新在上）；底部「加载更多」；点两次后第 2、3 页接在后面且**不重复**；翻到最后一页显示「没有更多了」。
- 预期：左侧灰色标签是跟进方式（电话/拜访/微信/邮件/备注/其他）或「阶段」。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**B4 编辑跟进：时间线立刻更新，客户「最后跟进」不变（裁定 8）**
- 操作：时间线上自己写的一条点「编辑」，改内容与跟进时间，保存。
- 预期：该条就地更新（不必刷新）。
- 预期：客户列表该客户的「最后跟进」日期**不变**（编辑不碰 `last_activity_at`）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**B5 删除跟进：二次确认后消失，刷新仍不在**
- 操作：自己写的一条点「删除」→ 确认。
- 预期：条目消失；`F5` 后仍不在；「已删除」不显示（软删后时间线不返回它）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**B6 阶段变更出现在时间线，金额是快照（裁定 4）**
- 操作：给该客户建一条机会（金额 `100000.00`），在看板把它从「初步接触」拖到「需求确认」，再编辑机会把金额改成 `200000.00`。
- 预期：时间线出现 `创建 → 初步接触`、`初步接触 → 需求确认` 两条「阶段」条目，
  第一条显示 `¥100,000.00`、第二条显示 `¥100,000.00`（**改金额之前**的快照），不是 `200,000.00`。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**B7 已删机会的历史条目：灰色标签且不可点**
- 操作：`admin` 建机会 `删除验证` → 改一次阶段 → `DELETE /api/v1/opportunities/{id}`（界面无删除入口，见附录 A.2）→ 回客户详情看时间线。
- 预期：该机会的阶段条目**仍在**，机会名后跟灰色 `机会已删除` 标签；**点不动**（不是链接）。
- 预期：同一条目上的操作人若已停用，另有一个灰色 `账号已停用`。
- 结论：☐ 通过　☐ 不通过（备注：____________）

### C. 权限与只读

**C1 公海客户全只读（含时间线行内按钮）**
- 账号：`sales1` → 苏州新能源电池科技有限公司（公海，`owner_id` 为空）。
- 预期：无「+ 写跟进」「+ 新建」、无悬浮按钮；时间线能看（若有历史）。
- 预期：**行内没有「编辑/删除」按钮**——即使这条是 `sales1` 自己以前写的（这是 T10 修补的实质缺口：
  之前只看"是不是我写的"，漏了"客户还写不写得动"，点下去必 403）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**C2 同部门同事的客户：看得到时间线，但他人记录没有按钮**
- 账号：`sales1` 打开 `boss`（李建国）名下的上海精工客户（P3 种子没有这种客户时，用 `boss` 先写一条跟进）。
- 预期：看得到时间线；`boss` 写的条目**没有**「编辑/删除」；`sales1` 自己写的有（若能写）。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**C3 越权客户：整页只有一句 403 文案**
- 账号：`sales1` 手动访问 `sales2` 名下客户（广州鼎盛）的 `/customers/{id}`。
- 预期：只显示「无权查看该客户」（后端 403 原文），**不渲染任何客户字段**，也不渲染待办卡/时间线；
  有「返回客户列表」链接。
- 结论：☐ 通过　☐ 不通过（备注：____________）

### D. 375px 移动端（§6.3）

**D1 悬浮「+ 跟进」只在窄屏出现**
- 操作：DevTools → `375 × 667` → 打开杭州智造详情。
- 预期：右下角出现圆形「+ 跟进」（距底 `bottom-20`，不压住内容也不压住底部导航）；点击能开写跟进弹窗。
- 操作：拉宽到 ≥1024px。预期：按钮消失，改用卡片右上角「+ 写跟进」。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**D2 详情页不横向滚动**
- 操作：375px 下（`document.documentElement.scrollWidth`）。
- 预期：`scrollWidth <= 375`；待办卡与时间线的长文本（公司名、待办内容、联系人邮箱）都在盒内换行；
  弹窗内容可滚动，底部按钮够得到。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**D3 首页在窄屏可读**
- 操作：375px 下打开首页。
- 预期：三块竖排；`共 N 条（显示前 M 条）` 与「完成」按钮不挤成一列乱码；客户名可点且不溢出。
- 结论：☐ 通过　☐ 不通过（备注：____________）

**D4 客户列表「最后跟进」按上海日期**
- 操作：375px 下看客户卡片上的「最后跟进 MM-DD」。
- 预期：与该客户在详情页时间线里最新一条的**上海日期**一致（不是 UTC 日期，也不是浏览器本地时区）。
  在 UTC 时区的机器/浏览器上验最有效：上海 10-31 早上 7 点写的跟进，UTC 机器上应显示 `10-31` 而不是 `10-30`。
- 结论：☐ 通过　☐ 不通过（备注：____________）

---

## 5. 已知限制（评审记录，P3 不修）

1. **时间线内没有可点跳转**：已删机会不给链接（点进去必然 404）；未删机会也不给，因为 P3 没有机会详情
   路由；联系人本来就没有单条页。P4 加了机会详情页后，`Timeline.tsx` 的机会名处换成 `Link` 即可。
2. **主管在时间线上没有编辑/删除入口**：后端允许主管删本部门记录（裁定 5），前端只对「本人或 admin」
   露按钮——判定主管/部门归属需要前端复算部门规则，保守起见不在前端做。
3. **时间线不含 `owner_change`**：`owner_change_logs` 表属 P4（裁定 6）。`type` 枚举与前端渲染分支已预留，
   游标也**明确拒绝**该类型（裁定 14），P4 建表后接入，前端契约不变。
4. **首页「即将进公海」的判定与展示口径不同**：判定按瞬时 `27 × 24h`（spec §4.2 原文），展示的
   `已 N 天无跟进` 按**上海自然日差**。边界上会出现「显示 27 天但还没进预警」（或反之），这是刻意的：
   判定忠于 spec，展示忠于用户理解。P4 若要一致，必须同时改 spec。
5. **「今天已过点」的待办仍算今日待办**：`due_at` 落在今天但已过时刻，仍留在「今日待办」不自动进逾期
   （spec §4.5 的逾期定义是 `due_at < 今天 0 点`）。前端在详情页待办卡里自行标红「已逾期」。
6. **金额 `0.00` 表示"当时没填"**：`stage_histories.amount_snapshot` 在机会金额为空时是 `0.00`，
   时间线显示 `¥0.00` 而不是"—"。P5 报表口径同样要处理这个歧义。
7. **未捕获异常返回纯文本**：500 的 body 是 `Internal Server Error`（纯文本），不是 `{code,message,detail}`。
   已知与 §5 的契约不一致，P4 收口统一异常处理器。
8. **`admin` 的首页也是"我名下客户"**：裁定 3 的字面口径（`customers.owner_id = me`），admin 名下没有客户，
   所以**admin 登录后首页三块恒为空**。这是按裁定执行的，不是缺陷；P4 若要"管理员看全公司"，需要新裁定。
9. **`.superpowers/sdd/**` 不入库**：`ledger.md`、`task-*-report.md`、`review-*.md` 只在本地（`.gitignore`）。
   交接给下一个会话时，能读到的只有本文件与 `docs/superpowers/handoffs/2026-11-01-p3-done-p4-next.md`。
10. **e2e 只覆盖 HTTP 层**：`test_e2e.py::test_p3_follow_up_loop` 证明"写跟进→待办→首页→完成→时间线"
    在服务端成立，但页面渲染、CSS、交互全在本文件 §4。前端没有自动化测试（spec §10 的决定，不是漏做）。

---

## 6. 验收记录

| 编号 | 项目 | 结论（通过/不通过） | 备注（截图或复现步骤） |
|---|---|---|---|
| A1–A6 | 首页三块：路由、造数据、完成、逾期边界、预警豁免 |  |  |
| B1–B7 | 详情页：待办卡、时间线、编辑/删除、阶段快照、已删标注 |  |  |
| C1–C3 | 权限：公海只读、同事记录、越权 403 |  |  |
| D1–D4 | 375px：悬浮按钮、无横滚、首页、最后跟进时区 |  |  |

任何一项不通过：先补一条自动化测试（接口类）或把复现步骤写进本节（交互类），再修；不要直接手改代码。

---

## 7. 部署前必改（P1/P2 四项继续有效）

1. `JWT_SECRET` 换成真随机（48 字节以上），否则启动只告警不阻断。
2. `COOKIE_SECURE=true`（refresh cookie 只在 HTTPS 下传）。
3. `ENVIRONMENT=production`：种子脚本只允许 `dev`/`test`，但仍要靠环境变量兜住。
4. 反向代理做登录限流（应用内的失败计数在单进程内存里）。
5. **上线前跑 `alembic upgrade head`**：P3 新增 `activities`、`follow_ups` 两张表；漏跑的表现是
   P3 接口 500（表不存在），不是 404。
6. 种子脚本**不要**在生产执行（自带 `dev`/`test` 守卫，`--force` 才绕过）。

---

## 附录 A：造数据 / 清理

### A.0 取 token（后面的脚本都用它）

```powershell
$base = "http://127.0.0.1:8081"
function Get-Token($user) {
  (Invoke-RestMethod -Method Post -Uri "$base/api/v1/auth/login" -ContentType 'application/json' `
    -Body ("{`"username`":`"$user`",`"password`":`"Passw0rd!`"}")).access_token
}
$h = @{ Authorization = "Bearer $(Get-Token 'sales1')" }
$adminH = @{ Authorization = "Bearer $(Get-Token 'admin')" }
```

### A.1 给客户补 21 条跟进（验时间线翻页，limit=20 → 会出第二页）

```powershell
$cid = (Invoke-RestMethod "$base/api/v1/customers?keyword=杭州智造" -Headers $h).items[0].id
1..21 | ForEach-Object {
  $body = @{ customer_id = $cid; type = "call"; content = ("翻页验证-{0:d2}" -f $_) } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri "$base/api/v1/activities" -Headers $h -ContentType 'application/json' -Body $body | Out-Null
}
Write-Output "已补 21 条；详情页时间线应出现「加载更多」"
```

### A.2 造"已删机会的历史"（验 B7）

```powershell
$cid = (Invoke-RestMethod "$base/api/v1/customers?keyword=杭州智造" -Headers $h).items[0].id
$opp = Invoke-RestMethod -Method Post -Uri "$base/api/v1/opportunities" -Headers $h -ContentType 'application/json' `
  -Body (@{ customer_id = $cid; name = "删除验证"; amount = "1000.00" } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$base/api/v1/opportunities/$($opp.id)/stage" -Headers $h -ContentType 'application/json' `
  -Body (@{ to_stage = "needs" } | ConvertTo-Json) | Out-Null
Invoke-RestMethod -Method Delete -Uri "$base/api/v1/opportunities/$($opp.id)" -Headers $adminH 2>$null
# 删除要主管/管理员权限：上面的 $adminH 若 403，改用 boss 的 token
Write-Output "机会 id=$($opp.id) 已软删；回客户详情看时间线应出现灰色「机会已删除」"
```

### A.3 回到干净种子状态

`scripts.seed` 幂等但**不覆盖**已改动数据；要回到出厂状态只能重建开发库：

```powershell
$psql = "E:\Wayne\Doc\CY_CRM\.tmp\pg\pgsql\bin\psql.exe"
& $psql -h 127.0.0.1 -p 55432 -U postgres -d easycrm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
cd E:\Wayne\Doc\CY_CRM\backend
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m scripts.seed
```

（开发库里没有要保留的数据时再用。注意：这一步会连同 P2 的机会与阶段历史一起清掉。
`easycrm_test` 不用管，pytest 每次自己重建 schema。）

### A.4 验收后

- 停掉多开的 8081 后端与 `npm run dev`，避免下次会话误连旧实例。
- 需要的话按 A.3 重置，把 A.1/A.2 造的 `翻页验证-*` 与已删机会清掉。