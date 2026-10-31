"""迁移与模型的漂移守护。

测试夹具用 ``Base.metadata.create_all`` 建表，生产用 Alembic 迁移，这是两个真值源：
迁移漏了某列时全部测试照样通过，只有线上才撞 NOT NULL / UndefinedColumn。本文件把
测试库清空后用 Alembic 从零升到 head，再从两个角度检查这份**迁移产物**：

- ``compare_metadata`` 比对模型与迁移（含类型与 server_default），有差异即失败；
- 直接查 ``pg_constraint`` 与裸 INSERT：CHECK 约束名、``server_default`` 是否真的落在
  迁移建出的表上。``compare_metadata`` 不比较 CHECK 约束，约束在迁移里被改名或删掉它
  不会报；这类只在迁移文件里改坏、模型没动的问题只有这里能发现。
"""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import app.db.models_registry  # noqa: F401  注册全部模型到 Base.metadata
from alembic import command
from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# 迁移里显式命名的 CHECK 约束。名字进首个迁移即固化，之后改名等于线上约束换了名字，
# 依赖约束名的排查与运维脚本会静默失效，所以这里按集合包含钉死（新增约束不算漂移）。
CHECK_CONSTRAINT_NAMES = {
    "activities": {
        "ck_activities_type",
    },
    "follow_ups": {
        "ck_follow_ups_status",
        "ck_follow_ups_done_at_consistency",
    },
    "opportunities": {
        "ck_opportunities_stage",
        "ck_opportunities_win_rate_range",
        "ck_opportunities_amount_non_negative",
        "ck_opportunities_lost_reason_required",
        "ck_opportunities_won_at_required",
    },
    "stage_histories": {
        "ck_stage_histories_from_stage",
        "ck_stage_histories_to_stage",
    },
    "pool_rules": {
        "ck_pool_rules_single_row",
    },
    "owner_change_logs": {"ck_owner_change_logs_action"},
    "audit_logs": {"ck_audit_logs_action"},
    "import_jobs": {"ck_import_jobs_kind", "ck_import_jobs_status"},
}

# CHECK 的**表达式**：``compare_metadata`` 不比 CHECK，名字断言只挡"被删/改名"。
# 模型侧枚举改了、迁移还是旧值时，名字照样在，只有 pg_get_constraintdef 能发现。
CHECK_CONSTRAINT_DEFINITIONS = {
    "ck_activities_type": ("call", "visit", "wechat", "email", "note", "other"),
    "ck_follow_ups_status": ("pending", "done", "cancelled"),
    "ck_follow_ups_done_at_consistency": ("done", "done_at IS NOT NULL"),
    "ck_owner_change_logs_action": [
        "claim",
        "recycle",
        "assign",
        "transfer",
        "release",
    ],
    "ck_audit_logs_action": [
        "create",
        "delete",
        "permission_change",
        "transfer",
        "import",
    ],
    "ck_import_jobs_kind": ("customers", "contacts", "opportunities"),
    "ck_import_jobs_status": (
        "validating", "validated", "importing", "done", "failed",
    ),
}


def _render(diff: list[object]) -> str:
    return "\n".join(f"  - {item}" for item in diff)


def _upgrade_to_head(engine: Engine) -> None:
    """清空 public schema 后从零升到 head。

    ``env.py`` 优先用 ``config.attributes["connection"]``（外部连接模式），迁移因此落在
    ``easycrm_test`` 而不是开发库；``engine.begin()`` 负责提交/回滚。
    """
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def test_migrations_match_models(engine: Engine) -> None:
    _upgrade_to_head(engine)

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            # compare_server_default 必须开：只比列与类型时，迁移里被删掉的
            # server_default 在模型侧看不出来（夹具走 create_all，默认值来自模型），
            # 线上却会在裸 INSERT 时才撞 NOT NULL。
            opts={"compare_type": True, "compare_server_default": True},
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], (
        "Alembic 迁移与 SQLAlchemy 模型不一致（共 "
        f"{len(diff)} 项），请补一条迁移或修正模型：\n{_render(diff)}"
    )


def test_migrated_schema_keeps_named_checks_and_column_defaults(engine: Engine) -> None:
    """约束名与 server_default 的守卫必须打在迁移产物上。

    ``conftest.py`` 的 ``db`` 夹具用 ``Base.metadata.create_all`` 建表，那边查到的约束名
    与默认值全部来自模型；迁移文件里把 CHECK 改名、或删掉 ``server_default``，模型侧一条
    都不会红。这里用 ``command.upgrade`` 建出的表直接查 ``pg_constraint``、再裸 INSERT
    验证默认值确实生效。
    """
    _upgrade_to_head(engine)

    with engine.begin() as connection:
        for table_name, wanted in CHECK_CONSTRAINT_NAMES.items():
            names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = CAST(:table_name AS regclass) "
                        "AND contype = 'c'"
                    ),
                    {"table_name": table_name},
                )
            }
            assert wanted <= names, f"{table_name} 缺少 CHECK 约束：{wanted - names}"

        # CHECK 表达式（pg_get_constraintdef）：名字在、表达式过时（模型枚举改了、
        # 迁移没跟上）也必须红——否则线上约束会放行新枚举旧值。
        for name, fragments in CHECK_CONSTRAINT_DEFINITIONS.items():
            definition = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = :name"
                ),
                {"name": name},
            ).scalar_one()
            for fragment in fragments:
                assert fragment in definition, (
                    f"{name} 的表达式缺少 {fragment}：{definition}"
                )

        # 裸 INSERT 只给必填列：默认值若没进迁移产物，这里撞的是 NOT NULL，
        # 不是"断言失败"——两种都红，正是这条用例要的。
        owner_id = connection.execute(
            text(
                "INSERT INTO users (username, password_hash, display_name, role) "
                "VALUES ('migration-probe', 'x', '迁移探针', 'sales') RETURNING id"
            )
        ).scalar_one()
        customer_id = connection.execute(
            text("INSERT INTO customers (name) VALUES ('迁移探针客户') RETURNING id")
        ).scalar_one()
        opportunity_id, stage, amount = connection.execute(
            text(
                "INSERT INTO opportunities (customer_id, name, owner_id) "
                "VALUES (:customer_id, '裸插入机会', :owner_id) "
                "RETURNING id, stage, amount"
            ),
            {"customer_id": customer_id, "owner_id": owner_id},
        ).one()
        assert stage == "contact"
        # 用 str 比较而不是 Decimal("0.00")：Decimal("0") == Decimal("0.00") 为真，
        # 那样比不出 numeric(14,2) 的 scale。
        assert str(amount) == "0.00"

        history_changed_at = connection.execute(
            text(
                "INSERT INTO stage_histories (opportunity_id, to_stage) "
                "VALUES (:opportunity_id, 'contact') RETURNING changed_at"
            ),
            {"opportunity_id": opportunity_id},
        ).scalar_one()
        assert history_changed_at is not None

        # 跟进记录：type 非法必须被数据库挡住（绕过 service 的写入也进不来）
        activity_at = connection.execute(
            text(
                "INSERT INTO activities (customer_id, type, content, activity_at, created_by) "
                "VALUES (:customer_id, 'call', '迁移探针跟进', now(), :owner_id) "
                "RETURNING activity_at"
            ),
            {"customer_id": customer_id, "owner_id": owner_id},
        ).scalar_one()
        assert activity_at is not None
        # 违规插入放进 SAVEPOINT：CHECK 失败会中止当前事务，若不回退保存点，后面的
        # 探针会全部撞 "current transaction is aborted"（Task 3 追加待办探针时暴露）。
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO activities (customer_id, type, content, activity_at, created_by) "
                    "VALUES (:customer_id, 'telepathy', '非法类型', now(), :owner_id)"
                ),
                {"customer_id": customer_id, "owner_id": owner_id},
            )

        # 待办：status 的 server_default 必须落在迁移产物上
        pending_status = connection.execute(
            text(
                "INSERT INTO follow_ups (customer_id, due_at, content, created_by) "
                "VALUES (:customer_id, now(), '迁移探针待办', :owner_id) RETURNING status"
            ),
            {"customer_id": customer_id, "owner_id": owner_id},
        ).scalar_one()
        assert pending_status == "pending"
        # done 却没有 done_at 必须被数据库挡住（同样用 SAVEPOINT 隔离）
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO follow_ups (customer_id, due_at, content, created_by, status) "
                    "VALUES (:customer_id, now(), '非法已完成', :owner_id, 'done')"
                ),
                {"customer_id": customer_id, "owner_id": owner_id},
            )

        # 公海规则：迁移必须插入 id=1 的默认行，且 id=1 单行约束在库上生效
        pool_row = connection.execute(
            text(
                "SELECT id, inactive_days, max_owned, auto_recycle_enabled, "
                "protect_with_open_deal, protect_with_pending_followup, recycle_notice_days "
                "FROM pool_rules"
            )
        ).one()
        assert tuple(pool_row) == (1, 30, 50, True, True, True, 3)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("INSERT INTO pool_rules (id) VALUES (2)"))

        owner_change_id = connection.execute(
            text(
                "INSERT INTO owner_change_logs "
                "(customer_id, action, to_owner_id, operator_id) "
                "VALUES (:customer_id, 'claim', :owner_id, :owner_id) RETURNING id"
            ),
            {"customer_id": customer_id, "owner_id": owner_id},
        ).scalar_one()
        assert owner_change_id is not None
        # 系统回收允许 operator_id 为 NULL（P4 裁定 6）
        connection.execute(
            text(
                "INSERT INTO owner_change_logs "
                "(customer_id, action, from_owner_id) "
                "VALUES (:customer_id, 'recycle', :owner_id)"
            ),
            {"customer_id": customer_id, "owner_id": owner_id},
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO owner_change_logs (customer_id, action) "
                    "VALUES (:customer_id, 'stolen')"
                ),
                {"customer_id": customer_id},
            )

        audit_id = connection.execute(
            text(
                "INSERT INTO audit_logs (entity, entity_id, action, before_json, after_json) "
                "VALUES ('customers', :customer_id, 'transfer', "
                "'{\"owner_id\": null}', '{\"owner_id\": 1}') RETURNING id"
            ),
            {"customer_id": customer_id},
        ).scalar_one()
        assert audit_id is not None
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO audit_logs (entity, entity_id, action) "
                    "VALUES ('customers', :customer_id, 'hacked')"
                ),
                {"customer_id": customer_id},
            )
