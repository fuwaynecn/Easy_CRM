"""客户 Excel 导入：模板、解析、校验、commit。

两阶段（design §4.6）：
1. 上传只解析与校验，落 `import_jobs` 与文件，状态 `validated` / `failed`；
2. commit 重新解析校验后单事务写客户，状态 `done`。

导入**不更新**已有客户：重名按 `normalize_name` 跳过，不覆盖（spec §4.6）。
"""

from io import BytesIO

from openpyxl import Workbook

# 列名顺序就是模板与解析的顺序；带 * 的是必填。改这里等于改导入契约。
CUSTOMER_TEMPLATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("客户名称*", "必填；与现有客户重名会被跳过，不会覆盖"),
    ("负责人*", "必填；填系统登录名 username，且必须已分配部门"),
    ("简称", "选填，最多 64 字"),
    ("编码", "选填，最多 64 字"),
    ("行业", "选填，最多 64 字"),
    ("地区", "选填，最多 64 字"),
    ("地址", "选填，最多 255 字"),
    ("网站", "选填，最多 255 字"),
    ("规模", "选填，最多 32 字"),
    ("等级", "A / B / C；留空按 C"),
    ("来源", "选填，最多 64 字"),
    ("采购方式", "招标 / 直采 / 框架 / 其他；留空不填"),
    ("预算状态", "已立项 / 待审批 / 无预算 / 未知；留空按未知"),
    ("客户状态", "活跃 / 停用 / 输单；留空按活跃"),
    ("备注", "选填"),
)

_TEMPLATE_HEADERS = tuple(name for name, _ in CUSTOMER_TEMPLATE_COLUMNS)
_TEMPLATE_EXAMPLE = (
    "杭州示例科技有限公司", "sales1", "示例科技", "", "制造业", "浙江", "", "",
    "50-200人", "B", "官网", "招标", "已立项", "活跃", "示例行，导入前请删除",
)


def build_customer_template() -> bytes:
    """生成客户导入模板：表头 + 一行示例 + 填写说明。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "客户"
    sheet.append(list(_TEMPLATE_HEADERS))
    sheet.append(list(_TEMPLATE_EXAMPLE))
    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["O"].width = 36

    guide = workbook.create_sheet("填写说明")
    guide.append(["列名", "说明"])
    for name, note in CUSTOMER_TEMPLATE_COLUMNS:
        guide.append([name, note])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
