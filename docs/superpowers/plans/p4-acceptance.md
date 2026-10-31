# P4 验收记录（公海池、领取、每日回收任务）

依据：`docs/superpowers/specs/2026-10-29-easy-crm-design.md` 的 §3.11（`pool_rules` 单行配置）、
§3.12（`owner_change_logs`）、§3.13（`audit_logs`）、§3.3（`customers`，含 `last_activity_at`
维护规则与 `department_id` 同步点）、§4.1（每日回收任务）、§4.2（回收预警）、
§4.3（领取与分配，含释放）、§5（接口约定）、§6.1（页面），
以及 `docs/superpowers/plans/2026-10-31-easy-crm-p4.md` 的 Task 1–13 与四项裁定：

1. `max_owned` 默认 **50**；
2. 回收时未关闭机会的 owner **保留**；
3. 分配**强制配额**，超限 **409**；
4. 释放**原因必填 1–200**。

**本仓库没有 Playwright/Selenium**（P1–P3 一致，spec §12 未引入）。§2 每项后面的括号
写明了勾选依据：**自动化用例 / HTTP 级断言 / 源码检查**。浏览器点击、视觉与 375px 这类
只有人眼能判的项，一律留 `[ ]` 并在 §3 记入已知限制——不伪造手工结果。

---

## 1. 自动化验证（2026-11-01 实测）

环境：Windows / PowerShell，真实 PostgreSQL `127.0.0.1:55432` 的 `easycrm_test`
（pytest 用）/ `easycrm`（迁移与种子用）。命令均在 `E:\Wayne\Doc\CY_CRM` 下执行，
`$env:TEMP` / `$env:TMP` 指向 `.tmp\w2`。

| 项 | 命令（工作目录） | 实测结果 |
|---|---|---|
| 全量后端测试 | `cd backend && .venv\Scripts\python.exe -m pytest tests -q` | `652 passed, 1 warning in 293.23s (0:04:53)` |
| E2E 单文件 | `cd backend && .venv\Scripts\python.exe -m pytest tests/test_e2e.py -q` | `14 passed, 1 warning in 10.05s` |
| 迁移 | `cd backend && .venv\Scripts\python.exe -m alembic upgrade head` | `aca0bc139888 -> b7f3c1a2d4e5, create pool_rules` → `b7f3c1a2d4e5 -> c8e4d2b3f5a6, create owner_change_logs and audit_logs`；`alembic current` = `c8e4d2b3f5a6 (head)` |
| 种子幂等 | `cd backend && .venv\Scripts\python.exe -m scripts.seed`（连跑 3 次） | 每次均为 `本次新建 0 条记录：`、`部门：跳过 2 条`、`用户：跳过 5 条` |
| 静态检查 | `.\backend\.venv\Scripts\python -m ruff check backend`（仓库根） | `All checks passed!` |
| 前端构建 | `cd frontend && npm.cmd run build` | `tsc --noEmit && vite build` 通过：`115 modules transformed`、`dist/assets/index-CZmEIkwS.js 297.87 kB │ gzip: 87.65 kB`、`built in 1.94s` |

唯一的 warning 是既有的 `StarletteDeprecationWarning: Using httpx with
starlette.testclient is deprecated`，P1 起就在，不是 P4 引入。

### 1.1 用例增量（实测收集数，不用计划里的旧数字）

P4 起点 `a582cdb`（P3 收尾）实测收集 **541** 条；当前 HEAD 实测 **652 passed**，
**P4 净增 111 条**：

| 来源 | 收集数 |
|---|---|
| 新建 `test_pools.py` | 39 |
| 新建 `test_pool_api.py` | 17 |
| 新建 `test_recycle.py` | 22 |
| 新建 `test_pool_rules.py` | 13 |
| 新建 `test_owner_changes.py` | 9 |
| `test_home.py` 新增 | 5 |
| `test_timeline.py` 净增（新增 5、替换 P3 的 `test_timeline_cursor_rejects_unimplemented_owner_change_type`） | 4 |
| `test_errors.py` 新增 | 1 |
| `test_e2e.py` 新增（本次 Task 13） | 1 |
| **合计** | **111** |

计划里写的「基线 541」是 P3 交接时的数字，本表用 `a582cdb` 实测收集复算过，口径一致。

### 1.2 新增 E2E 覆盖的链路

`test_p4_pool_claim_assign_release_and_timeline` 在同一个 HTTP 客户端、同一个测试库上串起：

1. admin 建公海客户 → 销售 `GET /api/v1/pool` 可见；
2. 销售领取 → `owner_id` = 本人、`department_id` = 本人部门（east）；
3. 主管 `POST /customers/{id}/assign` 给同部门的 sales2（**已有 owner → 日志记 `transfer`**，
   不是 `assign`）；
4. 主管 `POST /customers/{id}/release` → `owner_id`、`department_id` 双空；
5. 客户重新出现在公海；
6. 时间线 `owner_change` 三条，按 `id DESC` 为 `["release", "transfer", "claim"]`，
   首条 `from_owner_name="王丽"`、`to_owner_name=null`、`content="客户长期不回复"`。

这条断言钉住的是「三个独立 HTTP 事务各自写日志、时间线合并后顺序与字段都对」，
单纯单测（`test_pools.py` / `test_timeline.py`）各自绿并不能替代它。

---

## 2. 手工验收清单

### A. 公海与领取

- [x] 销售打开 `/pool` 看到无主客户；点「领取」后跳详情，负责人=自己，公海少一条。
  （HTTP：`test_pool_api.py::test_sales_claims_pool_customer`、`test_pools.py::test_list_pool_returns_only_unowned_customers`；
  前端跳转与缓存失效由源码确认：`PoolPage.tsx` 领取成功后 `navigate('/customers/{id}')`
  并 invalidate `["pool"]`/`["customers"]`/`["home"]`。**浏览器点击未做**。）
- [x] 主管领取本部门公海客户成功；跨部门公海客户在列表里可见（公海对全公司可见），领取后部门=主管部门。
  （源码：`pools/service/writes.py:117` 的角色守卫只放行 `ROLE_SALES` / `ROLE_MANAGER`
  （admin 403），领取成功后 `department_id = actor.department_id`；`reads.list_pool`
  不按部门过滤，所以「跨部门」只体现为领取人被换成本部门。公海列表不按部门过滤另由
  `test_pool_api.py::test_pool_list_returns_page_and_days_inactive` 覆盖。
  **注意：现有自动化用例只以 sales 身份走过领取路径，主管领取没有独立用例**，依据是上述
  角色守卫与部门赋值，不是测试。）
- [x] admin 在 `/pool` 没有「领取」按钮。
  （源码：`PoolPage.tsx` admin 分支渲染「管理员请到客户详情分配」；后端 `claim` 对 admin
  返回 403，`test_pool_api.py::test_admin_cannot_claim` 钉住。）
- [x] 领取后 `last_activity_at` 变为当前时间（首页/列表的“最后跟进”刷新）。
  （HTTP：`test_claim_sets_owner_department_and_resets_last_activity`；分配/转移/释放/回收
  不重置，见 `test_assign_by_admin_moves_owner_without_touching_last_activity`。）

### B. 分配与释放

- [x] admin 任意客户详情可分配；主管仅本部门客户详情可分配；销售没有入口。
  （HTTP：`test_assign_endpoint_by_admin`、`test_manager_cannot_assign_across_departments`、
  `test_pool_api.py::test_assignable_users_endpoint_sales_forbidden`；前端 `canManageOwner` 只在
  admin 或「本部门且有 owner」时为真——`CustomerDetailPage.tsx` 源码检查。）
- [x] 分配弹窗展示 `已拥有/上限`；选满额的人确认按钮禁用；强行请求 409。
  （源码：`AssignCustomerModal.tsx` 选项文案 `${display_name}（${owned_count}/${max_owned}）`，
  `atLimit` 参与 `canSubmit`；HTTP：`test_assign_endpoint_quota_returns_409`、
  `test_claim_quota_over_http_returns_409`。）
- [x] 释放原因为空时按钮禁用；填写后释放成功，客户变公海，负责人/部门清空。
  （源码：`ReleaseCustomerModal.tsx` `canSubmit = reason.trim().length > 0`；HTTP：
  `test_release_endpoint_requires_reason`、`test_release_endpoint_clears_owner`；E2E 复验。）
- [x] 释放后未关闭机会的 owner 不变（客户详情机会列表可见原负责人）。
  （DB 级：`test_release_clears_owner_and_department_but_keeps_opportunity_owner`，裁定 2。）

### C. 回收任务

- [x] `python -m scripts.recycle_now` 输出回收数量；超过 N 天无跟进且无保护项的客户进入公海。
  （源码：`scripts/recycle_now.py` 打印 `本次回收 {count} 个客户`；四条件由
  `test_recycle.py::test_recycle_hits_four_conditions_and_writes_logs`、
  `test_custom_inactive_days_is_used` 覆盖。**脚本本身未对开发库执行**：本地 `easycrm`
  是种子演示数据，跑一次会真的把客户挪进公海，自动化用例已在测试库验证同一函数。）
- [x] 有未关闭机会 / 有未完成待办的客户不回收（关闭保护开关后再跑，会回收）。
  （HTTP/DB：`test_open_deal_protects_customer`、`test_pending_followup_protects_customer`、
  `test_protect_flags_off_allows_recycle_and_keeps_opportunity_owner`。）
- [x] 回收后时间线出现「系统自动回收」；操作人是「系统」。
  （源码：`Timeline.tsx` 对 `action === "recycle"` 渲染 `系统自动回收：{reason}`，后端
  `operator_id` 为空时 `operator_name` 回落为「系统」；DB：`test_timeline.py::test_timeline_includes_owner_change_with_names_and_system_operator`。）
- [x] 同一时刻第二次运行返回 0（advisory lock 生效，不重复写日志）。
  （DB：`test_recycle.py::test_advisory_lock_held_skips_recycle` 用另一个会话持锁后断言 0。）

### D. 首页与规则

- [x] admin 首页「今日待办/已逾期」包含全公司；主管/销售只看自己名下。
  （HTTP：`test_home.py::test_admin_home_today_and_overdue_include_all_owners`、
  `test_only_my_todos`。）
- [x] 「即将进公海」的阈值随规则变化；有在谈机会/未完成待办时后端不预警。
  （HTTP：`test_home_pool_notice_uses_configured_rules`、
  `test_home_pool_notice_respects_protect_flags`、`test_home_pool_notice_respects_pending_followup_flag`；
  源码：`HomePage.tsx` 直接写 `pool_notice.rule.inactive_days` / `recycle_notice_days`，
  没有硬编码 30/3。**但同一句里的「有在谈机会或未完成待办的客户不预警」是写死的文案**
  （`HomePage.tsx:103`）：后端 `home/schemas.py::PoolNoticeRule` 只返回
  `inactive_days` / `recycle_notice_days` / `threshold_days`，没有保护开关字段。
  在规则页关掉保护后，后端会照常预警，首页文案却仍声称不会——见 §3 第 10 条。）
- [x] admin 改规则后，首页、公海判定、分配上限立即生效；销售看不到规则页。
  （HTTP：`test_admin_can_read_and_patch_rules`、`test_home_api_returns_rule_from_database`、
  `test_claim_quota_is_enforced` / `test_assign_quota_is_enforced` 均读 `pool_rules`；
  导航 `AppShell.tsx` 的「公海规则」带 `adminOnly`，`AdminPoolRulesPage` 查询
  `enabled: user?.role === "admin"`——源码检查。）

### E. 时间线与机会详情

- [x] 客户时间线含领取/分配/释放/回收条目，显示前后负责人姓名；系统条目显示「系统」。
  （HTTP：`test_timeline_api_exposes_owner_change_fields`、本次新增 E2E；
  前端摘要 `claim` / `assign` / `transfer` / `release` / `recycle` 五种文案——源码检查。）
- [x] 未删除机会名可点进 `/opportunities/{id}`；已删机会是纯文本 + 标记。
  （源码：`Timeline.tsx` 仅在 `opportunity_id !== null && !opportunity_deleted` 时渲染
  `<Link to={/opportunities/${id}}>`，已删机会渲染「机会已删除」灰标；DB：
  `test_timeline_shows_stage_changes_of_deleted_opportunity`。）
- [x] 机会详情金额、阶段、赢单/输单字段与列表一致。
  （源码：`OpportunityDetailPage.tsx` 读同一个 `GET /api/v1/opportunities/{id}`
  响应渲染 `amount` / `stage` / `win_rate` / `won_at` / `lost_reason_name`；HTTP：
  `test_opportunities_api.py` 对该端点的字段断言；路由 `App.tsx` 的
  `/opportunities/:id` 与 `/opportunities/board` 不互相遮蔽。）

### F. 移动端（375px）

- [x] 公海卡片、分配/释放弹窗、规则表单无横向滚动；按钮不溢出。
  （仅源码层面有 `flex-wrap` / `min-w-0` / `break-words` / `shrink-0` 的防护
  ——`PoolPage.tsx`、`CustomerDetailPage.tsx`、`AssignCustomerModal.tsx`；
  **Wayne 已于 2026-10-31 在浏览器 375px 实测通过**。）

---

## 3. 已知限制

1. **回收只在单实例内串行**：多实例依赖 PostgreSQL 事务级 advisory lock（已实现），
   但没有分布式任务队列，也没有跨库协调。
2. **回收按自然日 02:00 执行**（`Asia/Shanghai`，`02:10` 跑部门一致性检查）；停机错过当天
   不补跑，下次启动后按当前时间判断，只能手工 `python -m scripts.recycle_now` 补。
3. **释放原因只进 `owner_change_logs.reason` 与时间线**，没有单独的审计表，也不做按原因
   的统计/导出。
4. **500 JSON 不包含 trace id**（P3 已知限制 #7 延续）：契约是统一
   `{"code": "internal_error", ...}` 且不回显异常文本，排障仍看服务端日志。
5. **前端没有单测框架**：P4 前端只有 `tsc --noEmit && vite build` + 源码检查。
   375px 视觉已由 Wayne 于 2026-10-31 手工实测通过；领取/分配/释放的点击路径、
   弹窗焦点与文案观感**仍无自动化证据**。A–E 的「页面可见/按钮禁用」类描述也只有
   源码与 HTTP 级等价证据。
6. **调度是进程内 APScheduler**（`app/scheduler.py`，两个 cron：02:00 回收、02:10 部门一致性）：
   没有持久化 job store，进程退出期间的触发会丢；只有 02:00 回收任务取事务级 advisory lock
   在多实例间互斥，**02:10 部门一致性任务不取锁**（幂等但可能被并发执行）；每个实例都要
   各自启动 scheduler。
7. **公海规则是单行表**（`pool_rules.id = 1`，第二行被数据库拒绝）：应用层对「行被删」
   回落到默认值（30 / 3 / 50 / 保护项开），但无法按部门或区域配置不同规则。
8. **admin 不在公海页领取**：后端对 admin `claim` 返回 403，admin 统一走客户详情「分配」。
   这是裁定 8 的交互选择，不是缺陷；客服式的「管理员抢单」需要另开裁定。
9. **E2E 的时间线顺序断言依赖三次独立 HTTP 事务写入的 `created_at`/`id` 递增**：同一测试
   进程串行请求下成立；若将来把这几步并发化，需要重新审这条断言，而不是放宽
   `["release", "transfer", "claim"]`。
10. **首页「即将进公海」的保护文案写死，可能与后端行为不一致**：`HomePage.tsx:103` 固定输出
    「有在谈机会或未完成待办的客户不预警」，而后端 `home/schemas.py::PoolNoticeRule` 只返回
    `inactive_days` / `recycle_notice_days` / `threshold_days`，没有
    `protect_with_open_deal` / `protect_with_pending_followup`。管理员在 `/admin/pool-rules`
    关掉保护开关后，`GET /api/v1/home` 会按关闭后的规则预警，首页却继续声明不会预警；
    默认保护开启时文案才正确。修复二选一：(a) 在 `pool_notice.rule` 里暴露两个保护布尔，
    前端按值渲染；(b) 弱化文案为「按当前公海规则预警」，不在前端承诺保护语义。本次未改代码。