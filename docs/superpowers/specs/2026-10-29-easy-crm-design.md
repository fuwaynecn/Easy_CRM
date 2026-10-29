# Easy CRM 设计说明

> 状态：待审阅
> 日期：2026-10-29
> 范围：内部销售 CRM 第一版（MVP）

## 1. 背景与目标

公司销售团队 5-20 人，目前客户资料、销售机会、跟进情况没有统一载体，导致三个具体问题：

1. 客户归属不清——同一个客户被两个销售同时跟，或者客户被某个销售长期占着却不跟进。
2. 跟进靠记忆——约定的回访时间没人提醒，客户热度自然凉掉。
3. 管理者看不到过程——只知道月底成了几单，不知道在谈什么、卡在哪个阶段、为什么输单。

第一版要解决的就是这三件事，不追求做一个通用 CRM 产品。

### 1.1 目标

- 销售在手机或电脑上都能在 1 分钟内记完一次跟进，并预约下次跟进。
- 客户有明确且可追溯的归属；长期无跟进的客户自动回到公海，任何人可领取。
- 主管能看到本部门客户的完整过程和机会漏斗，老板能看到全公司。

### 1.2 非目标（第一版明确不做）

合同与回款、文件附件、产品价目表、邮件/微信推送通知、多租户、原生移动 App、线索（Lead）与客户的双实体设计。

关于线索：Odoo 与 Frappe CRM 都把“线索”和“机会”分开，但那是为了处理“市场部涌进大量未验证线索”的场景。本项目的客户来源是销售自己开发与转介绍，量小且已知，多一层线索转换只会增加录入负担。因此**客户即录入起点**，机会直接挂在客户下。

## 2. 用户与权限

### 2.1 角色

| 角色 | 标识 | 说明 |
|---|---|---|
| 管理员 | `admin` | 老板或 IT，拥有全部权限，含用户管理、公海规则、字典维护 |
| 主管 | `manager` | 带团队的销售主管，可见并管理本部门数据 |
| 销售 | `sales` | 一线销售，只见自己名下数据 |

一个账号只属于一个角色、一个部门。部门为单层结构，不做层级树。

### 2.2 数据作用域

作用域由角色决定，作用于客户、联系人、机会、跟进记录、待办：

| 角色 | 可见范围 | 可写范围 |
|---|---|---|
| `sales` | `owner_id = 自己` 的记录；公海客户只读 | 仅自己名下 |
| `manager` | `department_id = 自己部门` 的记录；公海客户只读 | 本部门全部 |
| `admin` | 全部 | 全部 |

除上表外还有一条实现口径：`sales` 可以编辑自己名下的客户，但**删除仅限 `manager` / `admin`**。

公海客户（`owner_id IS NULL`）对 `sales` 与 `manager` 均为**只读**，领取后才可编辑。这条是硬约束，防止销售之间互相改写他人池中的客户。

### 2.3 权限的结构性保证

权限不靠开发纪律，靠代码结构：

1. 所有读取必须经 `scoped_query(model, user)` 返回的作用域条件，路由层禁止直接 `select(Model)`。
2. 所有写操作前必须调用 `assert_writable(record, user)`，不在作用域内返回 403。
3. 每个列表接口必须配一条越权测试（B 查不到 A 的数据）。

跨模块访问数据必须走对方 service 暴露的方法，禁止直接 import 其他模块的 model 做查询。

## 3. 数据模型

PostgreSQL 16，字符集 UTF-8。所有表带 `created_at`、`updated_at`（UTC，`timestamptz`）；业务主表带 `deleted_at`（软删除）。金额统一 `numeric(14,2)`，币种 CNY。

### 3.1 departments

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| name | varchar(64) unique not null | 部门名称 |

### 3.2 users

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| username | varchar(64) unique not null | 登录名 |
| password_hash | varchar(255) not null | Argon2id（`pwdlib` recommended，m=64MiB, t=3, p=4） |
| display_name | varchar(64) not null | 姓名 |
| role | varchar(16) not null | `admin` / `manager` / `sales` |
| department_id | bigint FK departments | `admin` 可为空 |
| status | varchar(16) not null default 'active' | `active` / `disabled` |
| last_login_at | timestamptz | |

登录失败策略：同一账号连续 5 次失败锁定 15 分钟，锁定状态**落库**（`users.failed_login_count` / `users.locked_until`），不记进程内缓存：线上跑 2 个 uvicorn worker，进程内计数不共享，记在缓存会变成"每个 worker 各自数 5 次"。

### 3.3 customers

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| name | varchar(200) not null | 客户全称 |
| short_name | varchar(64) | 简称 |
| code | varchar(64) | 内部编号，可空 |
| industry | varchar(64) | 行业 |
| region | varchar(64) | 地区 |
| address | varchar(255) | 地址 |
| website | varchar(255) | 官网 |
| scale | varchar(32) | 规模 |
| level | varchar(4) not null default 'C' | `A` / `B` / `C`，默认 `C` |
| source | varchar(64) | 来源渠道 |
| procurement_type | varchar(16) | 采购方式：`tender` 招标 / `direct` 直采 / `framework` 框架协议 / `other` 其他，可空 |
| budget_status | varchar(16) not null default 'unknown' | 预算状态：`approved` 已立项 / `pending` 预算未批 / `none` 无预算 / `unknown` 未知 |
| status | varchar(16) not null default 'active' | `active` / `lost` / `inactive` |
| owner_id | bigint FK users null | **空即为公海** |
| department_id | bigint FK departments null | 冗余字段，随 owner 同步 |
| last_activity_at | timestamptz | 最后有效跟进时间，回收判定依据 |
| remark | text | |
| created_by | bigint FK users | |

索引：`(owner_id)`、`(department_id)`、`(last_activity_at)`、`(status)`、`(name)`。`lower(name) varchar_pattern_ops`（重名查询）在 P1 **不建**：P1 没有前缀 LIKE 查询，重名查重走应用层软查重（Python 侧 `lower(name)` 比较）；如将来要建，必须显式命名——`ix` 命名模板对函数索引会与普通列索引撞名。

**不设 `name` 唯一约束。** 现实中有“XX 集团”与“XX 集团分公司”这类同名不同实体。改为创建时软查重（见 4.4）。

`last_activity_at` 的维护规则只有三条，避免歧义：
1. 新建一条 activity 时，置为该 activity 的 `activity_at`（补录历史跟进时不回退，取 `max(原值, activity_at)`）；
2. 领取或转移客户时，置为当前时间；
3. 其余编辑字段、改阶段、改联系人**均不更新**。

`department_id` 的同步点仅四处：创建客户、领取、分配/转移、所属销售调整部门。另设一个每日一致性检查任务，发现不一致时记录 WARN 日志并自动修正。

### 3.4 contacts

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| customer_id | bigint FK customers not null | |
| name | varchar(64) not null | |
| title | varchar(64) | 职位 |
| phone | varchar(32) | |
| wechat | varchar(64) | |
| email | varchar(128) | |
| is_primary | boolean not null default false（数据库默认值） | 主要联系人 |
| decision_role | varchar(32) | 决策人 / 技术负责人 / 采购 / 使用者 / 其他 |
| remark | text | |

索引：`(customer_id)`、`(phone)`。
同一客户下允许手机号重复输入（不做唯一约束），仅在 UI 提示“该客户下已有此号码的联系人”。

主联系人**至多一条、可以为 0**（新客户可以没有主联系人）；切换主联系人时服务层把旧的置 false，数据库再用部分唯一索引 `uq_contacts_customer_primary`（谓词含 `deleted_at IS NULL`）兜底。删除主联系人**不自动补位**，下次由用户显式指定。

### 3.5 opportunities

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| customer_id | bigint FK customers not null | |
| name | varchar(200) not null | 机会名称 |
| stage | varchar(16) not null | 见 3.6 阶段枚举 |
| amount | numeric(14,2) default 0 | 预计金额 |
| expected_close_date | date | 预计成交日期 |
| competitor | varchar(200) | 主要竞争对手（自由文本，多个用顿号分隔） |
| owner_id | bigint FK users not null | |
| win_rate | smallint | 赢单率百分比，默认由阶段推导 |
| lost_reason_id | bigint FK lost_reasons null | 输单时必填 |
| lost_note | text | 输单补充说明 |
| won_at | timestamptz null | 赢单时间 |
| created_by | bigint FK users | |

索引：`(customer_id)`、`(owner_id)`、`(stage)`、`(expected_close_date)`。

**机会负责人与客户负责人保持一致。** 客户的领取、分配、转移在同一个事务内把该客户名下**未关闭**（`stage NOT IN ('won','lost')`）的机会一并改到新负责人。已赢单/输单的机会保留原负责人，作为历史业绩归属，不参与同步——否则把三年前的成单算到新人头上，业绩报表就失真了。

### 3.6 阶段枚举与赢单率

阶段为**四个推进阶段加两个终态**，不做可配置——只有一条销售线，可配置的复杂度换不来收益。

| 阶段 | 标识 | 默认赢单率 |
|---|---|---|
| 初步接触 | `contact` | 10 |
| 需求确认 | `needs` | 30 |
| 方案报价 | `proposal` | 50 |
| 商务谈判 | `negotiation` | 70 |
| 赢单 | `won` | 100 |
| 输单 | `lost` | 0 |

阶段变更规则：
- 允许前进、后退、跨阶段跳转，均写一条 `stage_histories`。
- `won` / `lost` 为终态。重开终态机会需选新阶段并填写重开原因，同样写 history，操作人限 `manager` / `admin`。
- 进入 `lost` 必须选择 `lost_reason_id`；进入 `won` 记录 `won_at`。
- 任何阶段变更由 service 层强制写 history，路由层无法绕过。

### 3.7 stage_histories

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| opportunity_id | bigint FK opportunities not null | |
| from_stage | varchar(16) null | 创建时为空 |
| to_stage | varchar(16) not null | |
| amount_snapshot | numeric(14,2) | 变更时的金额 |
| note | text | 重开原因等 |
| operator_id | bigint FK users | |
| changed_at | timestamptz not null default now() | |

索引：`(opportunity_id, changed_at)`。这是漏斗与成交周期报表的唯一数据来源。

### 3.8 activities（已发生的沟通记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| customer_id | bigint FK customers not null | |
| contact_id | bigint FK contacts null | |
| opportunity_id | bigint FK opportunities null | |
| type | varchar(16) not null | `call` / `visit` / `wechat` / `email` / `note` / `other` |
| content | text not null | |
| activity_at | timestamptz not null | 实际发生时间，可为过去 |
| created_by | bigint FK users not null | |

索引：`(customer_id, activity_at desc)`、`(created_by, activity_at)`。

`note` 类型承担“客户备注”职责，不单独建表——备注本来就是一种发生在某个时间点、由某人写下的记录，合在 activity 里才能进同一条时间线。

### 3.9 follow_ups（未来的待办）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| customer_id | bigint FK customers not null | |
| contact_id | bigint FK contacts null | |
| opportunity_id | bigint FK opportunities null | |
| due_at | timestamptz not null | 到期时间（含时分） |
| content | text not null | 要做的事 |
| status | varchar(16) not null default 'pending' | `pending` / `done` / `cancelled` |
| done_at | timestamptz | |
| created_by | bigint FK users not null | |

索引：`(status, due_at)`、`(customer_id, status, due_at)`。

**待办不设独立的负责人字段。** 待办跟着客户走：客户转给谁，待办就归谁。查询“我的今日待办”即 join customers 后按 `customers.owner_id = me` 过滤。两个真值源（客户负责人 + 待办负责人）一定会打架。

为什么 activities 与 follow_ups 拆两张表：若把“下次跟进时间”挂在跟进记录行上，提醒逻辑就要“每客户取最新一行并判断是否已完成”，容易重复提醒、容易漏。拆开后首页待办就是 `status='pending' AND due_at <= 今天`，语义单一。

### 3.10 lost_reasons（输单原因字典）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| name | varchar(64) unique not null | |
| sort_order | int default 0 | |
| active | boolean default true | |

初始数据：价格过高、交期不满足、技术方案不匹配、客户预算取消、客户选择了竞品、项目延期搁置、其他。

字典表而非自由文本，输单原因才能作为报表维度。管理员可增删改。

### 3.11 pool_rules（单行配置）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| id | smallint PK | 1 | 固定单行 |
| inactive_days | int | 30 | 无跟进多少天进公海 |
| max_owned | int | 50 | 单人最多持有客户数 |
| auto_recycle_enabled | boolean | true | 自动回收总开关 |
| protect_with_open_deal | boolean | true | 有未关闭机会不回收 |
| protect_with_pending_followup | boolean | true | 有未完成待办不回收 |
| recycle_notice_days | int | 3 | 回收前预警天数 |

### 3.12 owner_change_logs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| customer_id | bigint FK customers not null | |
| action | varchar(16) not null | `claim` / `recycle` / `assign` / `transfer` / `release` |
| from_owner_id | bigint FK users null | |
| to_owner_id | bigint FK users null | |
| reason | text | |
| operator_id | bigint FK users not null | |
| created_at | timestamptz not null default now() | |

### 3.13 audit_logs

只记高风险动作，不记普通字段编辑（否则表会爆）。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| entity | varchar(32) not null | 表名 |
| entity_id | bigint not null | |
| action | varchar(32) not null | `create` / `delete` / `permission_change` / `transfer` / `import` |
| before_json | jsonb | |
| after_json | jsonb | |
| operator_id | bigint FK users | |
| created_at | timestamptz not null default now() | |

索引：`(entity, entity_id)`、`(created_at desc)`。

### 3.14 import_jobs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| kind | varchar(16) not null | `customers` / `contacts` / `opportunities` |
| file_name | varchar(255) | |
| status | varchar(16) not null | `validating` / `validated` / `importing` / `done` / `failed` |
| total_rows | int | |
| success_rows | int | |
| error_rows | int | |
| errors_json | jsonb | `[{row, column, message}]` |
| created_by | bigint FK users | |
| created_at | timestamptz | |

## 4. 业务流程

### 4.1 每日回收任务

每日 02:00 由进程内 APScheduler 触发一次。任务开始先取 PostgreSQL advisory lock，取不到即跳过（防止将来多副本重复执行）。

回收判定，**四个条件同时满足**：

```
owner_id IS NOT NULL
AND status = 'active'
AND last_activity_at < now() - inactive_days
AND 该客户名下无 stage NOT IN ('won','lost') 的机会     -- protect_with_open_deal
AND 该客户名下无 status='pending' 的待办                 -- protect_with_pending_followup
```

命中则：`owner_id = NULL`、`department_id = NULL`、写 `owner_change_logs(action='recycle')`、写 `audit_logs`。

保护性豁免的理由：把一单正在谈的客户收走，比漏收一个僵尸客户造成的伤害大得多。有 pending 待办说明有人在安排下一步动作，同样不算僵尸。

### 4.2 回收预警（不落表）

首页“即将进公海”区块直接查询：

```
owner_id IS NOT NULL
AND status = 'active'
AND last_activity_at < now() - (inactive_days - recycle_notice_days) 天
AND 满足 4.1 的另外两条豁免条件
```

预警**不写通知表**。落表就要维护状态同步（客户一旦被跟进，预警要消失），必然产生“已跟进了预警还在”的脏数据。直接查询是幂等的。

### 4.3 领取与分配

- **领取**（`sales` / `manager`）：校验 `owner_id IS NULL` 且本人持有数 < `max_owned`。用 `SELECT ... FOR UPDATE` 行锁，抢锁失败返回 409“该客户已被他人领取”。成功后 `owner_id = me`、`department_id = 我的部门`、`last_activity_at = now()`，未关闭机会的 `owner_id` 同步为本人，写日志。
  - 重置 `last_activity_at` 是必须的：否则一个 40 天无跟进的客户被领取后，次日凌晨立刻被自己回收。
- **分配**（`manager` 本部门 / `admin` 全部）：指定目标销售，不重置 `last_activity_at`，未关闭机会同步转移，写日志。目标销售需为 `active` 状态且仍有配额。
- **释放**（主动放回公海）：仅 `manager` / `admin` 可操作，或原负责人在有转岗/离职场景下由管理员代为操作。

**P1 没有领取功能。** 因此 `admin` 创建且未指定负责人的客户会落在公海：`admin` 可以编辑这些客户，`sales` / `manager` 只能只读；P2 上线领取后，这些客户可被正常领取。

### 4.4 客户重名处理

创建客户时，后端将输入名称归一化（**先做 NFKC 全角转半角**，再去空格、去“（）”等噪声字符、转小写）后做模糊匹配，命中则返回 409，body 内含相似客户列表（最多 5 条）。前端弹窗展示“可能重复”，用户确认后带 `force=true` 重新提交。

> NFKC 这层是为 Excel 导入准备的：`杭州ＸＸ公司` 与 `杭州XX公司` 必须判为同一个名字，否则一次导入就会造出成对的重复客户。副作用是把 `㈱`→`(株)`、`①`→`1` 一类兼容字符折叠，这是期望行为。

不设数据库唯一索引，因为强制唯一会卡住真实业务（同名集团与分公司、带后缀的同一法人主体）。

### 4.5 跟进与待办

写跟进时，表单底部“下次跟进”区块（时间 + 内容）可选填。填了则在同一个事务内创建 `follow_ups(pending)`。

完成待办：`status='done'`、`done_at=now()`。界面提供“顺便写条跟进记录”复选项，勾选则把待办内容复制为一条 `activity(type='visit' 或按用户选择)` 并关联同一客户/联系人，同时按 3.3 规则更新 `customers.last_activity_at`。

逾期定义：`due_at < 今天 00:00 (Asia/Shanghai)` 且 `status='pending'`。

### 4.6 Excel 导入

**两阶段导入**：上传并校验 → 人工确认 → 写入。不做“上传即写库”。

流程：

1. 用户下载模板（后端生成 `.xlsx`，含表头、示例行、列说明）。
2. 上传文件，后端存至 `/data/imports/{job_id}.xlsx`，返回 job_id 与校验结果。
3. 校验逐行进行，返回 `total_rows / error_rows / errors: [{row, column, message}]`。前端“下载错误清单”，用户改完重传。
4. 用户确认后，后端**重新读取并再次全量校验**，通过才写入。

校验规则：

| 项 | 规则 |
|---|---|
| 日期 | 接受 `YYYY-MM-DD`、`YYYY/MM/DD`、Excel 序列号；`YYYY.MM.DD` 也接受 |
| 阶段 | 中文名映射到枚举（如“方案报价”→`proposal`），无法映射报错 |
| 采购方式 / 预算状态 | 中文映射到枚举（“招标”→`tender`、“已立项”→`approved`）；留空时预算状态取 `unknown` |
| 金额 | 去千分位、去“元”“¥”“CNY”、去空格 |
| 负责人 | 按 `users.username` 匹配；匹配不到报错，不静默置空 |
| 客户（联系人/机会导入时） | 按 `customers.name` 精确匹配；匹配不到报错 |
| 客户重名 | 已存在则跳过并在结果中记为“已存在，跳过”，**不覆盖** |
| 必填 | 客户名称、联系人姓名、机会名称与客户、负责人 |
| 行数上限 | 单次 5000 行 |

写入策略：分批，每 1000 行一个事务；任一行出错整批回滚并返回错误明细。选择“全有或全无”而非“尽力而为”，是因为半成功的数据最难收拾——用户不知道哪些进去了。

## 5. 接口约定

- 前缀 `/api/v1`，REST，资源名复数。
- 成功直接返回资源或分页体；错误统一 `{code, message, detail}`。
- 分页：`?page=1&page_size=20`，响应 `{items, total, page, page_size}`。`page_size` 上限 200。
- 例外：`GET /api/v1/customers/{cid}/contacts` **不分页**（一个客户的联系人量级很小），直接返回数组，与其它列表的 `{items, total}` 约定不同。
- 列表筛选通用参数：`keyword`（名称/联系人/电话模糊匹配）、`owner_id`、`status`、`procurement_type`、`budget_status`、`stage`、`level`、日期区间 `date_from` / `date_to`、`sort`（白名单字段 + `-` 前缀表倒序）。
  - `keyword` 在 P1 只对客户字段（名称、简称、编码）以及**该客户下未删除联系人的姓名/电话**生效；机会、待办相关字段的搜索留给后续阶段。
- 认证：access token 存前端内存（2 小时），refresh token 用 `httpOnly` + `Secure` + `SameSite=Lax` cookie（7 天）。不使用 localStorage 存 token。页面刷新或 access token 过期时，前端用 refresh cookie 静默换取新 access token；refresh 失败则跳登录页。
- 时区：数据库与接口一律 UTC（ISO 8601 带偏移），前端按 `Asia/Shanghai` 展示，“今天”的判断在后端统一用该时区。
- 越权返回 403 而非 404（内部系统，明确告诉用户没权限比假装不存在更利于排障）。

主要资源端点（示意，非完整清单）：

```
POST   /auth/login   /auth/refresh   /auth/logout
GET    /me
CRUD   /customers          GET /customers/{id}/timeline
CRUD   /contacts
CRUD   /opportunities      POST /opportunities/{id}/stage
CRUD   /activities
CRUD   /follow_ups         POST /follow_ups/{id}/done
GET    /pool               POST /pool/{customer_id}/claim
POST   /customers/{id}/assign   /transfer
GET    /reports/funnel  /reports/win-rate  /reports/cycle  /reports/performance  /reports/activities  /reports/lost-reasons
POST   /imports             GET /imports/{id}   POST /imports/{id}/commit
GET    /imports/{kind}/template
GET    /admin/users  /admin/pool-rules  /admin/lost-reasons
```

`GET /customers/{id}/timeline` 合并三类记录按时间倒序返回：activities、stage_histories（该客户名下所有机会的阶段变更）、owner_change_logs。分页游标用 `(occurred_at, type, id)`。

## 6. 前端

技术栈 React 18 + TypeScript + Vite + Tailwind CSS，状态用 TanStack Query（服务端状态）+ 少量本地状态，不引入 Redux。

### 6.1 页面

| 路由 | 内容 |
|---|---|
| `/login` | 账号密码 |
| `/` | 首页：今日待办 / 已逾期 / 即将进公海 |
| `/customers` | 客户列表，可按负责人·等级·地区分组；批量改负责人、改等级 |
| `/customers/:id` | 详情：左栏字段 + 联系人，右栏时间线 |
| `/opportunities` | 机会列表 |
| `/opportunities/board` | 看板，按阶段分列，拖拽改阶段 |
| `/pool` | 公海池，一键领取 |
| `/reports` | 漏斗、赢单率、平均成交周期、销售业绩、跟进量、输单原因 |
| `/import` | 上传 → 校验预览 → 确认导入 |
| `/admin/users` `/admin/pool-rules` `/admin/lost-reasons` | 仅 `admin` |

### 6.2 交互骨架

借鉴 Odoo 的四件事，视觉用 Tailwind 重做：

1. 列表视图带分组与批量操作（勾选后底部浮出操作条）。
2. 点行打开右侧抽屉看详情，不整页跳转；抽屉内可继续编辑。
3. 详情里一条统一时间线，跟进、阶段变更、归属变更混排，支持在顶部直接写一条。
4. 机会看板拖拽换阶段，落位即调 `POST /opportunities/{id}/stage`，失败回弹。

### 6.3 移动端

断点 `<768px`：

- 列表改为卡片，每卡显示客户名、等级、负责人、最后跟进时间、待办标识。
- 详情全屏，时间线优先于字段区。
- 全局右下角悬浮按钮“+跟进”，主路径是先记录再补充细节，而不是先找到客户再点按钮。
- 机会看板在手机上横向滚动分列，不做拖拽（触屏拖拽误触率高），改为点卡片后选阶段。

## 7. 报表口径

| 指标 | 口径 |
|---|---|
| 漏斗 | 当前各阶段的机会数与金额合计（存量快照，非流量转化） |
| 赢单率 | 期间内 `won 数 / (won + lost 数)`，按机会数，不按金额。期间按终态到达时间（`won_at` 或输单时的 `changed_at`）归属，不按机会创建时间 |
| 平均成交周期 | `won_at - 机会创建时间` 的天数均值，按负责人、按月份分组 |
| 销售业绩 | 期间内赢单金额合计，按 `owner_id` 与月份分组 |
| 跟进量 | 期间内 activities 条数，按 `created_by` 分组，按天/周聚合 |
| 输单原因 | 按 `lost_reason_id` 分组，输出条数与金额合计 |
| 竞争对手 | 按 `opportunities.competitor` 文本分组，输出机会数、金额合计与其中赢单率。同一机会填了多个对手时按顿号拆分后分别计入，故合计会大于机会总数 |

默认统计区间为本月，可切换本月/上月/本季/本年/自定义。

## 8. 目录结构

```
CY_CRM/                    # 本地工作目录；远程仓库为 fuwaynecn/Easy_CRM
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/          # 配置、安全、依赖注入、分页
│   │   ├── db/            # session、base、scope
│   │   └── modules/
│   │       ├── auth/ users/ departments/
│   │       ├── customers/ contacts/
│   │       ├── opportunities/ activities/ followups/
│   │       ├── pools/ reports/ imports/
│   │       └── 每个包含 models.py / schemas.py / service.py / router.py
│   ├── alembic/
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/{api,pages,components,shared}/
│   ├── vite.config.ts
│   └── Dockerfile
├── deploy/                # Caddyfile、备份脚本、systemd unit
├── docker-compose.yml
└── docs/
```

## 9. 部署与运维

目标环境：云服务器 Ubuntu 22.04，2 核 4G，带域名。

`docker-compose` 三个服务：

| 服务 | 镜像 | 说明 |
|---|---|---|
| `caddy` | caddy:2-alpine | 自动 HTTPS、托管前端静态文件、反代 `/api` 到 api |
| `api` | 自建（python:3.12-slim + uvicorn 2 worker） | 含 APScheduler 回收任务 |
| `db` | postgres:16 | 数据卷挂载 |

选 Caddy 而非 nginx + certbot：三行配置完成证书申请与续期，少一个独立故障点。

- 环境变量全部走 `.env`，`.env` 入 `.gitignore`，仓库只提交 `.env.example`
- 数据库迁移随部署执行 `alembic upgrade head`
- 备份：宿主机 cron 每日 03:00 `pg_dump -Fc`，本地保留 30 天，`rsync` 到对象存储。**上线时必须做一次真实恢复演练**，未验证的备份视为不存在。
- 日志：uvicorn access/error 日志入文件并轮转；慢查询（>500ms）单独记录

## 10. 测试策略

后端 pytest + httpx（ASGI 直连，不起真实服务），使用独立 PostgreSQL 测试库。**不用 SQLite**——JSONB、`FOR UPDATE`、`timestamptz` 行为不同，用 SQLite 测等于没测。

四类必写测试：

1. **越权**：每个列表接口一条“销售 B 查不到销售 A 数据”的断言；写接口一条 403 断言。
2. **公海规则**：回收判定的条件组合，重点覆盖两条豁免（有在谈机会、有待办）与“领取后 `last_activity_at` 重置”。
3. **导入校验**：三种日期格式、中文阶段映射、重名跳过、错误行号与列名定位、整批回滚。
4. **端到端冒烟**：登录 → 建客户 → 建机会 → 阶段推进到赢单 → 写跟进并预约待办 → 完成待办并生成 activity。

前端不做自动化单测，维护一份 8 条以内的关键路径手测清单（登录、录客户、录机会、拖阶段、写跟进、看首页待办、领取公海客户、手机端录入）。

## 11. 交付里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P1 | 登录、用户、部门、客户、联系人、权限作用域 | 可真实录入客户并由主管查看部门数据 |
| P2 | 机会、看板、阶段历史 | 机会可拖拽推进，漏斗数据正确 |
| P3 | 跟进记录、待办、首页三块 | 完整日常闭环；手机端录入可用 |
| P4 | 公海池、领取、每日回收任务 | 回收规则全部按预期生效 |
| P5 | 报表、Excel 导入 | 六项报表口径与手算一致；导入 500 行无差错 |
| P6 | 部署上线、种子数据、恢复演练 | 域名可访问、HTTPS 正常、备份可恢复 |

P1–P3 完成即可投入日常使用，后续阶段不阻塞。

## 12. 已决定的设计取舍

记录这些是为了将来回看时知道当时为什么这么定，避免反复讨论。

| 取舍 | 决定 | 理由 |
|---|---|---|
| 线索与客户是否分开 | 不分，客户即起点 | 客户来源量小且已知，多一层转换只增加录入负担 |
| 跟进记录与待办是否同表 | 分表 | 提醒逻辑需要单一语义，同表会导致“取最新一行”的重复提醒问题 |
| 待办是否有独立负责人 | 无，跟客户走 | 两个真值源必然冲突 |
| 客户名是否唯一约束 | 不设，软查重 + 强制创建 | 硬约束会卡住同名集团与分公司 |
| 阶段是否可配置 | 固定五阶段 | 单销售线，配置复杂度换不来收益 |
| 有在谈机会/待办是否回收 | 不回收 | 误收在谈客户的代价远大于漏收僵尸客户 |
| 回收预警是否落表 | 不落表，实时查询 | 落表需维护状态同步，必然产生脏数据 |
| 导入重名客户 | 跳过不覆盖 | 批量改坏现有客户资料是最难排查的事故 |
| 越权返回 403 还是 404 | 403 | 内部系统，明确告知无权限更利于排障 |
| 前端单测 | 不做 | 该规模下投入产出比不划算，改为手测清单 |
| Odoo 是否作为底座 | 不作为；仅借鉴交互骨架 | 前端无法独立复用（OWL 组件与后端视图元数据强耦合），采用其前端等于采用整套 Odoo |

## 13. 已知风险

| 风险 | 影响 | 应对 |
|---|---|---|
| 权限作用域漏写 | 客户数据串号，信任崩塌 | `scoped_query` 结构性约束 + 每接口越权测试 |
| 手机端录入体验差导致销售不用 | 系统形同虚设 | P3 阶段即交付手机端并让 1-2 名销售真实使用一周再进 P4 |
| 首次导入数据脏 | 上线即返工 | 两阶段导入 + 错误清单 + 整批回滚 |
| 定时任务与请求并发 | 回收瞬间客户正被编辑 | 回收在事务内对客户行加 `FOR UPDATE`；领取用行锁 |
| 单人维护 Odoo 式框架的认知负担 | 已规避 | 本项目为自研，无框架黑盒 |