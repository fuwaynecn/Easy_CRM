"""公海规则常量（design §3.11 / §4.2 的 P3 只读版本）。

P4 会建 `pool_rules` 单行配置表，并把这里的三个常量换成读表。现在不建表、不读环境
变量：一个"看起来可配置"的假象比写死更难排查（改了环境变量没生效、或者首页与回收
任务读了两份配置）。

`NOTICE_THRESHOLD_DAYS = INACTIVE_DAYS - RECYCLE_NOTICE_DAYS`（spec §4.2 原文
`now() - (inactive_days - recycle_notice_days)`）：先预警、后回收，两者共用同一份
默认值，P4 换成读表时也只改一处。
"""

INACTIVE_DAYS = 30
RECYCLE_NOTICE_DAYS = 3
NOTICE_THRESHOLD_DAYS = INACTIVE_DAYS - RECYCLE_NOTICE_DAYS
