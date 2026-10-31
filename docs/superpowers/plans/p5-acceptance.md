# P5 验收记录（报表 / 统计 + 客户 Excel 导入）

依据：`docs/superpowers/specs/2026-10-29-easy-crm-design.md` 的 §7（报表口径）、§4.6（Excel
导入）、§5（接口约定）、§6（前端），以及 `docs/superpowers/plans/2026-10-31-easy-crm-p5.md`
的 Task 1–13 与五项裁定（报表金额读时点快照、作用域按机会负责人当前部门、`import_jobs`
增 `skipped_rows`、竞争对手报表保留、`error_rows` 按出错行数计）。

**本仓库没有 Playwright/Selenium**（P1–P4 一致，spec §12 未引入），因此本文件的证据分三类：
**自动化用例 / HTTP 级断言 / 浏览器运行时实测**。每一条后面写明依据；没有实测证据的项一律
留 `[ ]` 并写清谁补，不伪造手工结果。

---

## 1. 自动化验证（2026-11-01 实测）

环境：Windows / PowerShell；真实 PostgreSQL 16。最终可信全量在隔离实例 **55433** 的 `easycrm_test` 上串行执行（同版本 PG16、每用例重建 schema；55432 上的并发尝试结果已作废，避免同库互踩）。其余命令在 `E:\Wayne\Doc\CY_CRM` 下执行，`$env:TEMP` / `$env:TMP` 指向 `.tmp\w2`。

| 项 | 命令（工作目录） | 实测结果 |
|---|---|---|
| Ruff | `cd backend && .venv\Scripts\ruff.exe check app tests` | `All checks passed!`（exit 0） |
| E2E 单文件 | `cd backend && .venv\Scripts\python.exe -m pytest tests/test_e2e.py -q` | `16 passed, 1 warning in 12.43s` |
| 后端全量 | `cd backend && $env:TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55433/postgres'; .venv\Scripts\python.exe -m pytest -q` | **`695 passed, 1 warning in 429.03s (0:07:09)`**（隔离实例 55433；失败历史见 §1.2，已修复） |
| 迁移 | `cd backend && .venv\Scripts\python.exe -m alembic upgrade head` | exit 0（库已在 head，无 `Running upgrade` 输出）；`alembic current` → `d9a1c3e5f7b8 (head)` |
| 前端构建 | `cd frontend && npm.cmd run build` | `tsc --noEmit && vite build` exit 0：`119 modules transformed`；`dist/index.html 0.40 kB (gzip 0.27)`、`dist/assets/index-Dfw2ObZD.css 17.39 kB (gzip 3.84)`、`dist/assets/index-ZFtuAPrk.js 311.30 kB (gzip 91.15)`；`built in 2.27s` |

唯一的 warning 是既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is
deprecated`，P1 起就在，不是 P5 引入。

### 1.1 用例增量（实测收集数）

P4 基线 **652**（`p4-acceptance.md` §1）→ 当前 **695 收集**，P5 净增 **43**：

| 来源 | 收集数 |
|---|---|
| 新建 `tests/test_reports.py` | 20 |
| 新建 `tests/test_reports_api.py` | 4 |
| 新建 `tests/test_imports.py` | 17 |
| `tests/test_e2e.py` 新增（本次 Task 13） | 2 |
| **合计** | **43** |

本 brief 的估算「P4 基线 652 + 报表 24 + 导入 17 + E2E 2 = 695」与实际收集数一致；最终全量为 **695 passed**（见 §1.2 的修复记录）。

### 1.2 全量中出现过的 P3 时间炸弹（已修复，非 P5）

Task 13 首次全量在 2026-11-01 01:42（上海）执行时是 `694 passed, 1 failed`，失败项 `tests/test_home.py::test_home_api_shape`：

```
assert [] == ['今天的待办']
```

根因不是 P5 代码，而是这条 P3 用例**把日期写死又打了真实时钟的接口**：

- 夹具用 `NOW = 2026-10-31 01:00 UTC`（上海 2026-10-31 09:00）建待办 `due_at`；
- 断言走 `GET /api/v1/home`，服务端 `home/service.py` 用**真实当前时间**切「今日」区间
  `[上海零点, 次日零点)`；
- 2026-11-01 01:42（上海）时这条待办已落到昨天，于是进入「已逾期」，
  `today.items` 为空。

该用例单独运行同样失败，证明它与 P5、与测试执行顺序都无关：它是一条**过了 2026-10-31 就必红**的时间炸弹。控制器批准了**独立的测试修复提交** `1662b45`：把 `test_home_api_shape` 的 `due_at` 改成「上海今天 01:00」（`datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=1,...)`），不再依赖固定日期；其余使用确定性 `summary(..., now=NOW)` 的用例未动。修复后 `test_home.py` 单独 19 passed，全量 **695 passed**。

---

## 2. 报表口径对账（自动化，非另起手工数据集）

本节**不是**另准备一套手工数据，而是对 `backend/tests/test_reports.py` 里既有的确定性夹具
断言逐项抄录：两个部门（华东销售部 / 华南销售部）、两名销售（销售A 在华东、销售B 在华南）、受控机会；夹具时间基准
`NOW = 2026-10-31 15:00 UTC`（上海 23:00）；除注明外区间 `2026-10-01..2026-10-31`。
这些断言在真实 PostgreSQL 上执行；报表 service 与 HTTP 端点共用同一实现（`reports/router.py`
只是 `resolve_period` + 调 service），因此「页面/接口实际」就是同一条断言的通过结果。

| 报表 | 口径 | 测试 | 期望 / 实际 | 结论 |
|---|---|---|---|---|
| 漏斗 | 当前阶段机会数 + 最新阶段历史的金额快照 | `test_funnel_uses_latest_snapshot_not_current_amount` | won `count=1`、`amount=1000.00`；contact `count=0`；阶段顺序 `contact,needs,proposal,negotiation,won,lost` —— 同一断言通过（service/HTTP 同一实现） | 一致 |
| 赢单率 | 期间内赢单到达 /（赢单 + 输单到达） | `test_win_rate_uses_terminal_arrival_in_period` | won 1、lost 1、total 2、rate `50.0` —— 同一断言通过（service/HTTP 同一实现） | 一致 |
| 平均成交周期 | `won_at − created_at` 的均值 | `test_cycle_uses_won_at_minus_created_at` | `won_count=1`、`avg_days=10.0`；`by_owner[0].owner_name="销售A"`；`by_month[0].month="2026-10"` —— 同一断言通过（service/HTTP 同一实现） | 一致 |
| 销售业绩 | 最后一条 won 历史的 `amount_snapshot` | `test_performance_uses_won_snapshot_amount` | `won_count=1`、`total_amount=1000.00`（赢单后机会现值改 9999，报表不变）、`by_owner[0].amount=1000.00`、`by_month[0].month="2026-10"` —— 同一断言通过（service/HTTP 同一实现） | 一致 |
| 跟进量 | 期间 `activity_at` 条数，空桶补 0 | `test_activities_fill_empty_day_buckets` | total 3；桶 `2026-10-01:2, 2026-10-02:0, 2026-10-03:1`；`by_owner[0]` 销售A `count=3` —— 同一断言通过（service/HTTP 同一实现） | 一致 |
| 输单原因 | 期间 lost 历史条数 + 快照金额 | `test_lost_reasons_counts_and_snapshot_amount` | `total_count=3`、`total_amount=600.00`；价格高 2 条 /`300.00`，预算取消 1 条 /`300.00`（输单后把金额改成 9999 不影响快照） —— 同一断言通过（service/HTTP 同一实现） | 一致 |
| 竞争对手 | 按顿号拆分，同机会多对手分别计 | `test_competitors_splits_by_dunhao_and_counts_win_rate` | `total_opportunities=2`；西门子 count 2 / won 1 / lost 1 / rate `50.0`；ABB count 1 / won 1 / rate `100.0` —— 同一断言通过（service/HTTP 同一实现） | 一致 |

这些是真实 PostgreSQL 上对同一口径的自动化断言；人工页面抽查见 §4。

补充（区间与作用域口径，均有独立用例）：默认区间按上海时区本月（`test_period_defaults_to_current_month_in_shanghai`）、
单边区间与倒置区间 `400`（`test_period_rejects_single_bound` / `test_period_rejects_reversed_range`）、
已删客户/机会不计（`test_funnel_excludes_deleted_customer_and_opportunity`）、按负责人当前部门
（`test_funnel_scope_by_owner_department` / `test_win_rate_groups_by_current_owner_department`）、
跟进量按作者部门（`test_activities_scope_by_creator_department`）、周桶从周一起（`test_activities_week_bucket_starts_monday`）、
重开不计赢单（`test_win_rate_excludes_reopened_opportunity`）、跨月按上海时区（`test_cycle_month_bucket_uses_shanghai_timezone`）。

---

## 3. 导入验收

逐项证据取自 `backend/tests/test_imports.py`（17 条）与 `backend/tests/test_e2e.py`（2 条），
外加控制端浏览器运行时记录（2026-11-01；实现者未开浏览器）。

- [x] **模板可下载、Excel 可打开，表头/示例/说明齐全**：运行时 `/import` 真下载到
  `customers-template.xlsx` 6,349 字节（控制端浏览器实测，2026-11-01，375px 窗口）；自动化
  `test_template_has_headers_example_and_guide`（两个 sheet `客户` / `填写说明`、表头等于
  `CUSTOMER_TEMPLATE_COLUMNS`（15 列）、示例行 `杭州示例科技有限公司` / `sales1`）、
  `test_template_endpoint_requires_manager_and_customers_kind`
  （manager 200 + xlsx content-type；sales 403；`contacts` 400）。
- [x] **3 行合法文件：校验通过 → 确认写入 → 客户列表可见**：运行时 admin 上传 2 行走通
  `validated → done success 2`（控制端浏览器实测，2026-11-01）；E2E `test_p5_import_then_report_e2e`
  导入 3 行 → `success_rows=3` → `GET /customers?keyword=E2E` `total=3` → 建机会 → 漏斗可见；
  自动化 `test_commit_creates_customers_and_single_audit`（2 行，逐字段/部门/创建人）。
- [x] **500 行文件：一次导入成功**：`test_p5_import_500_rows` PASS（包含在 E2E 单文件
  16 passed / 12.43s 内；上传 `total_rows=500`、`error_rows=0`，commit `success_rows=500`）。
- [x] **重名（含空格/全半角差异）跳过，不覆盖，不计错误**：`test_upload_duplicate_name_is_skipped_not_error`、
  `test_validate_existing_duplicate_stays_out_of_valid`（尾随空格，命中 `normalize_name`）、
  `test_validate_in_file_duplicate_keeps_one_valid`（文件内同名）、`test_commit_in_file_duplicate_writes_one`
  （commit 层：写 1 条、跳过 1 条，库里只有 1 条）、`test_commit_skips_duplicate_created_after_upload`
  （上传后、commit 前被建成同名 → 不写、计 skipped）。全半角（NFKC）本身由
  `test_customers.py::test_normalize_name_normalizes_character_width` 与
  `test_customers.py::test_create_customer_rejects_full_width_duplicate` 钉住；导入侧沿用同一函数，
  **导入路径下的全角变体没有独立用例**，已覆盖的是尾随空格变体。
- [x] **未知负责人：行号 + 列名 + 原因，整体不写入**：`test_upload_unknown_owner_is_row_error`
  （`status=failed`、`errors[0].row=2`、`column="负责人*"`、消息含 `ghost`）；上传阶段不写客户由
  `test_upload_valid_rows_creates_validated_job` 的 `Customer` 计数 0 钉住。
- [x] **manager 导其他部门负责人：报错，不写入**：`test_upload_manager_cannot_use_other_department_owner`
  （`status=failed`，消息含「本部门」）；服务层判定在 `validate_customer_rows`
  （`actor.role == manager and owner.department_id != actor.department_id`）。
- [x] **校验失败后 commit 被拒（400）**：`test_commit_rejects_failed_or_done_job`（failed →
  400「校验未通过，请修正后重新上传」；done → 400「该导入任务已完成」，两条消息可区分）。
- [x] **commit 后改负责人状态：再次 commit 被拒且无部分写入**：`test_commit_revalidates_owner_and_writes_nothing`
  （上传后把负责人置 `disabled` → commit 400 `invalid_value`，库里 0 条）。
- [x] **审计日志只有 1 条 `action=import`**：`test_commit_creates_customers_and_single_audit`
  （`len(logs) == 1`，`entity_id` = job id，`after_json` 逐字段对齐
  `kind/file_name/total_rows/success_rows/skipped_rows`）。
- [x] **导入文件落盘路径与 `settings.import_dir` 一致**：`test_upload_valid_rows_creates_validated_job`
  （`tmp_path/imports/{job_id}.xlsx` 存在，且**字节等于上传原文**——commit 重新解析的就是这份文件）。

附带钉住但不在清单里的失败路径（同一文件）：`test_commit_marks_failed_when_stored_file_corrupt`
（落盘文件损坏 → job `failed`、`error_rows=1`、400 而不是 500 后留在 `validated`）、
`test_commit_rolls_back_on_audit_failure`（审计写失败 → 客户与审计整体回滚，再用独立事务把 job
标 `failed`）、`test_job_visibility_is_owner_scoped`、`test_upload_rejects_sales_and_unknown_kind`。

---

## 4. 手工 UI 检查（375px）

证据来源：P4 验收（Wayne 2026-10-31 浏览器实测）+ P5 控制端运行时实测（2026-11-01，
后端 `8081` + Vite，浏览器 375px）。截图在 `.tmp/`（`import-validated-375.png`、
`import-done-375.png`、`import-errors-375.png` 等），**未入库**（`.gitignore` 忽略 `.tmp`）。

- [x] **公海 / 分配释放弹窗 / 规则表单 375px 无横向溢出**：P4 `p4-acceptance.md` §2-F，
  Wayne 2026-10-31 实测通过。
- [x] **`/reports` 375px 无横向溢出、表格内部滚动**：控制端浏览器实测（2026-11-01）：七个报表接口
  对 admin/boss/sales1 全部 200；`granularity=month` → 400 `invalid_value`；
  `documentElement.scrollWidth == clientWidth == 375`；竞争对手表格 384px 在 317px 容器内滚动，
  页面无横向溢出。
- [x] **`/import` 375px 可操作、错误表不撑破页面**：控制端浏览器实测（2026-11-01）：模板真下载
  6,349 字节；2 行上传同步 `validated` → commit `done` success 2；错误工作簿 → `failed`，
  错误表 317px 落在 317px 容器内，页面无横向溢出；错误 CSV `import-2-errors.csv` 带 BOM
  `EFBBBF` 与引号转义；validated / done / error 三态 `documentElement.scrollWidth == clientWidth == 375`。
- [x] **「导入」仅 manager/admin 可见**：控制端浏览器实测（2026-11-01）：销售直接访问 `/import`
  被重定向回 `/`，导航里没有「导入」。
- [ ] **登录页 / 客户列表 / 机会列表 在 375px 无横向溢出**：未单独覆盖（前端无单测；P6 补）。
  P4 验收只覆盖公海相关页面，P5 运行时只量了 `/reports` 与 `/import`，这三页本次没有重新量测。
- [ ] **导航在 375px 可展开/收起**：未单独覆盖（前端无单测；P6 补）。导航项按角色隐藏已实测，
  但折叠/展开交互本身没有记录。
- [ ] **KPI 卡不挤压**：未单独覆盖（前端无单测；P6 补）。源码是 `grid grid-cols-2 sm:grid-cols-4`，
  375px 下两列换行；没有空数据下的截图。
- [ ] **空数据时显示「—」/「暂无数据」，不是 NaN/undefined**：未单独覆盖（前端无单测；P6 补）。
  源码层已确认 `ReportsPage.tsx` 的 `formatRate` / `formatDays` 对 `null` 返回 `—`，表格空数据行
  渲染「暂无数据」；但本次运行时的数据集非空，**没有浏览器实证**，因此不勾选。
- [ ] **`/import` 无任务时的空态**：未单独覆盖（前端无单测；P6 补）。控制端浏览器实测直接上传，
  没有停留在「尚无导入任务」的初始态。

---

## 5. 权限矩阵

| 页面 / 接口 | admin | manager | sales | 证据 |
|---|---|---|---|---|
| `GET /reports/funnel` | 全公司 | 本部门 | 本人 | `reports/common.py::opportunity_scope_conditions`；`test_funnel_scope_by_owner_department`（1 / 1 / 2） |
| `GET /reports/win-rate` | 全公司 | 本部门 | 本人 | 同上；`test_win_rate_groups_by_current_owner_department` |
| `GET /reports/cycle` / `performance` / `lost-reasons` | 全公司 | 本部门 | 本人 | 同上（各报表都先套 `opportunity_scope_conditions`） |
| `GET /reports/activities` | 全公司 | 本部门 | 本人 | `activity_scope_conditions`（按跟进**作者**当前部门，不按客户归属）；`test_activities_scope_by_creator_department` |
| `GET /reports/competitors` | 全公司 | 本部门 | 本人 | 同 `opportunity_scope_conditions`；`test_competitors_splits_by_dunhao_and_counts_win_rate` |
| `GET /imports/customers/template` | 200 | 200 | **403** | `ManagerOrAdminUser`；`test_template_endpoint_requires_manager_and_customers_kind` |
| `POST /imports`（上传） | **201** | **201**，但**负责人限本部门** | **403** | `ManagerOrAdminUser` + `validate_customer_rows` 部门判定；`test_upload_rejects_sales_and_unknown_kind`、`test_upload_manager_cannot_use_other_department_owner` |
| `POST /imports/{id}/commit` | 任意 job | 仅自己创建的 job | **403** | `commit_import_job` → `get_import_job`；非法角色先被 `ManagerOrAdminUser` 挡下 |
| `GET /imports/{id}` | 任意 | **仅自己创建**（他人 404） | **403** | `get_import_job(actor.role != admin and job.created_by != actor.id → 404)`；`test_job_visibility_is_owner_scoped` |
| `/reports` 页面 | 可见 | 可见 | 可见（数据自动收窄） | `AppShell.tsx` NAV 无角色限制；前端不做过滤，权限只在后端 |
| `/import` 页面 | 可见 | 可见 | **重定向 `/`** | `AppShell.tsx` NAV `managerOrAdmin` 隐藏 + `ImportPage.tsx` 页面级闸门（控制端浏览器实测，2026-11-01） |

两条口径要写清，避免 P6 误改：

1. **报表不复用 `customer_query`**：公海客户对所有主管可见，按客户可见性统计会让同一条公海机会
   在每个部门各算一次；所以按**机会负责人当前部门**归属（P5 裁定）。
2. **前端隐藏不是权限**：`/import` 只有视觉隐藏 + 页面跳转，真正的边界是后端
   `ManagerOrAdminUser` 与 `get_import_job`。

---

## 6. 已知限制

1. **报表金额是时点快照**：读 `stage_histories.amount_snapshot`；赢单后单独改金额、不换阶段，
   报表不变（也不会产生历史）。这是 P3 裁定，不是 bug。
2. **部门按当前负责人回算**：转岗后历史业绩会跟着人走；没有历史部门快照。
3. **重开机会不计入原赢单期间**：重开会清 `won_at`，所以它不再算期内赢单。
4. **竞争对手按顿号拆分**：同一机会多个对手分别计入，金额合计可大于机会总额。
5. **导入只支持客户、只支持 `.xlsx`、只新建不更新、不导出**：单次 5000 行 / 5MB。
6. **导入跳过重名不返回对方 id/名称**（避免越权枚举）；跳过数量只有计数。
7. **无站内信/邮件通知**；通知与多实例调度留给 P6。
8. **前端无单测**：`tsc --noEmit && vite build` 是唯一门禁；375px 与空态靠人工。
9. **`commit` 没有并发锁**：同一 job 被两个请求同时 commit 时，没有行锁/幂等键；前端在
   pending 时禁用按钮，但**API 客户端必须自己避免并发 commit**。延迟到 P6。
10. **损坏/丢失文件失败只记 `error_rows=1`**：上传阶段的 `total_rows` / `skipped_rows` 保留，
    前端会同时看到「总行数 N、错误 1 行」，这是刻意的（重传前保留统计），不是丢数据。
11. **校验是同步的**：上传在请求里完成解析与逐行校验（5000 行级别可接受），前端**没有轮询**；
    `getImportJob` 是为将来轮询预留的导出，目前页面未使用。
12. **页面 375px 只覆盖了公海（P4）、报表与导入（P5 运行时）**：登录页、客户列表、机会列表
    本次没有重新量测；报表/导入的空态没有浏览器实证。P6 补。

---

## 7. P6 建议顺序

1. 站内信 + 待办提醒（复用 `follow_ups`，不要新建通知表除非需要已读状态）；
2. APScheduler 单实例约束落地（api 单 worker 或外置 cron），写入部署文档；
3. 客户导出（与导入共用列映射，先做 xlsx）；
4. 联系人/机会导入（复用 `import_jobs`，补 `kind` 分支与模板）；
5. 报表索引优化（用 `EXPLAIN ANALYZE` 看 `stage_histories` 窗口查询，数据量上来再加）；
6. 审计日志查询页（admin）。