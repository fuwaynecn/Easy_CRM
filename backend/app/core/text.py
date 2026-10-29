"""LIKE/ILIKE 模式构造：用户输入的关键字只能按字面量匹配。

``%`` 与 ``_`` 在 LIKE 里是通配符。直接把用户输入拼进 ``%...%`` 会让搜索
``%`` 退化成"匹配全部"，搜索 ``a_b`` 时把 ``axb`` 也带出来——用户看到的是
"搜索结果不听话"，而不是"我输入了通配符"。SQLAlchemy 的 ``ilike(pattern,
escape="\\")`` 只负责告诉数据库哪个字符是转义符，输入里的转义符、``%``、``_``
必须先自己转义。

用户列表与客户列表共用这一处，避免两边对同一个关键字给出不同结果。
"""

# PostgreSQL 的 ESCAPE 子句只接受单个字符，统一用反斜杠。
LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """转义 LIKE 模式中的特殊字符（含转义符自身，顺序不能反）。"""
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def contains_pattern(value: str) -> str:
    """构造"包含该字面量"的 LIKE 模式，配合 ``escape=LIKE_ESCAPE`` 使用。"""
    return f"%{escape_like(value)}%"
