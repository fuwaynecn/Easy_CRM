import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    UnauthorizedError,
    WeakPasswordError,
)
from app.core.security import hash_password, verify_password
from app.modules.departments.service import create_department
from app.modules.users.schemas import PasswordReset, UserCreate
from app.modules.users.service import (
    ROLE_SALES,
    authenticate,
    create_user,
    get_user_by_username,
    reset_password,
    set_user_status,
)


def _make_sales_user(db, *, username="zhangsan", password="Passw0rd!"):
    dept = create_department(db, "华东销售部")
    return create_user(
        db,
        username=username,
        password=password,
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )


def _fail_login(db, times, *, username="zhangsan", password="bad"):
    for _ in range(times):
        with pytest.raises(UnauthorizedError):
            authenticate(db, username, password)


def test_password_hash_roundtrip():
    hashed = hash_password("Passw0rd!")
    assert hashed != "Passw0rd!"
    assert verify_password("Passw0rd!", hashed) is True
    assert verify_password("wrong", hashed) is False


@pytest.mark.parametrize("dirty_hash", ["", "not-a-hash", None])
def test_verify_password_returns_false_for_dirty_hash(dirty_hash):
    """外部导入的脏哈希必须返回 False，不能抛异常（否则登录路径 500）。"""
    assert verify_password("x", dirty_hash) is False


def test_verify_password_logs_exception_type_only(caplog):
    """脏哈希要留下可诊断的日志（异常类型名），但绝不能带明文口令或哈希本身。"""
    plain = "S3cret-Passw0rd!"
    dirty = "not-a-hash"
    with caplog.at_level(logging.DEBUG, logger="app.core.security"):
        assert verify_password(plain, dirty) is False
    messages = [r.getMessage() for r in caplog.records if r.name == "app.core.security"]
    logged = [m for m in messages if m.startswith("口令校验失败: ")]
    assert logged
    assert all(m.split(": ", 1)[1].isidentifier() for m in logged)
    for message in messages:
        assert plain not in message
        assert dirty not in message


def test_user_create_and_password_reset_repr_hide_password():
    created = UserCreate(
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
    )
    reset = PasswordReset(new_password="NewPass!23")
    assert "Passw0rd!" not in repr(created)
    assert "NewPass!23" not in repr(reset)
    assert created.password.get_secret_value() == "Passw0rd!"
    assert reset.new_password.get_secret_value() == "NewPass!23"


def test_short_password_is_400_business_error_without_plaintext(db):
    """口令长度不在 schema 层校验，因此不会有 422 的 input 回显；由 service 抛 400。"""
    plain = "Ab123"
    payload = UserCreate(
        username="shorty",
        password=plain,
        display_name="短口令",
        role=ROLE_SALES,
    )
    # schema 接受该输入，说明约束没有挂在字段上（否则 422 已在此抛出）
    assert payload.password.get_secret_value() == plain

    with pytest.raises(WeakPasswordError) as excinfo:
        create_user(
            db,
            username=payload.username,
            password=payload.password.get_secret_value(),
            display_name=payload.display_name,
            role=payload.role,
            department_id=None,
        )
    error = excinfo.value
    assert error.status_code == 400
    assert error.code == "weak_password"
    assert plain not in str(error)
    assert plain not in repr(error)
    assert plain not in error.message
    assert error.detail is None
    assert get_user_by_username(db, "shorty") is None


def test_reset_password_rejects_short_password_and_keeps_old_hash(db):
    user = _make_sales_user(db)
    old_hash = user.password_hash
    with pytest.raises(WeakPasswordError) as excinfo:
        reset_password(db, user.id, "Ab123")
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "weak_password"
    assert "Ab123" not in str(excinfo.value)
    assert user.password_hash == old_hash


def test_create_user_stores_hash_not_plaintext(db):
    dept = create_department(db, "华东销售部")
    user = create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    assert user.id is not None
    assert user.password_hash != "Passw0rd!"
    assert verify_password("Passw0rd!", user.password_hash)


def test_username_must_be_unique(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    with pytest.raises(ConflictError):
        create_user(
            db,
            username="zhangsan",
            password="Passw0rd!",
            display_name="张三二号",
            role=ROLE_SALES,
            department_id=dept.id,
        )


def test_get_user_by_username_returns_none_when_absent(db):
    assert get_user_by_username(db, "nobody") is None


def test_authenticate_success_updates_last_login(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    user = authenticate(db, "zhangsan", "Passw0rd!")
    assert user.last_login_at is not None


def test_authenticate_wrong_password_counts_failure(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    with pytest.raises(UnauthorizedError):
        authenticate(db, "zhangsan", "bad")
    user = get_user_by_username(db, "zhangsan")
    assert user.failed_login_count == 1


def test_authenticate_locks_after_five_failures(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    for _ in range(5):
        with pytest.raises(UnauthorizedError):
            authenticate(db, "zhangsan", "bad")
    user = get_user_by_username(db, "zhangsan")
    assert user.locked_until is not None
    with pytest.raises(LockedError) as excinfo:
        authenticate(db, "zhangsan", "Passw0rd!")
    assert excinfo.value.status_code == 423
    assert "锁定" in excinfo.value.message


def test_lock_threshold_is_exactly_five_failures(db):
    """第 4 次失败后仍不锁定，第 5 次才锁定：阈值必须精确为 5。"""
    _make_sales_user(db)
    _fail_login(db, 4)
    user = get_user_by_username(db, "zhangsan")
    assert user.failed_login_count == 4
    assert user.locked_until is None

    _fail_login(db, 1)
    user = get_user_by_username(db, "zhangsan")
    assert user.locked_until is not None


def test_locked_account_rejects_correct_password_with_423(db):
    _make_sales_user(db)
    _fail_login(db, 5)
    with pytest.raises(LockedError) as excinfo:
        authenticate(db, "zhangsan", "Passw0rd!")
    assert excinfo.value.status_code == 423
    assert "锁定" in excinfo.value.message


def test_login_succeeds_after_lock_expires_and_resets_counter(db):
    _make_sales_user(db)
    _fail_login(db, 5)
    user = get_user_by_username(db, "zhangsan")
    assert user.locked_until is not None

    # 不等 15 分钟：直接把锁定到期时间拨到过去
    user.locked_until = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    # 到期后再失败一次，让计数非零，才能验证成功登录确实把计数清零
    _fail_login(db, 1)
    assert get_user_by_username(db, "zhangsan").failed_login_count == 1

    authed = authenticate(db, "zhangsan", "Passw0rd!")
    assert authed.failed_login_count == 0
    assert authed.locked_until is None


def test_reset_password_clears_failures_and_lock_immediately(db):
    _make_sales_user(db)
    _fail_login(db, 3)
    user = get_user_by_username(db, "zhangsan")
    user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    db.commit()

    updated = reset_password(db, user.id, "NewPass!23")
    assert updated.failed_login_count == 0
    assert updated.locked_until is None

    authed = authenticate(db, "zhangsan", "NewPass!23")
    assert authed.failed_login_count == 0
    assert authed.locked_until is None


def test_authenticate_unknown_user_and_wrong_password_share_message(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    with pytest.raises(UnauthorizedError) as missing:
        authenticate(db, "nobody", "Passw0rd!")
    with pytest.raises(UnauthorizedError) as wrong:
        authenticate(db, "zhangsan", "bad")
    assert missing.value.message == wrong.value.message
    assert missing.value.status_code == wrong.value.status_code == 401


def test_disabled_user_cannot_authenticate(db):
    dept = create_department(db, "华东销售部")
    user = create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    set_user_status(db, user.id, "disabled")
    with pytest.raises(ForbiddenError) as excinfo:
        authenticate(db, "zhangsan", "Passw0rd!")
    assert excinfo.value.status_code == 403
    assert "停用" in excinfo.value.message


def test_reset_password_changes_hash(db):
    dept = create_department(db, "华东销售部")
    user = create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    updated = reset_password(db, user.id, "NewPass!23")
    assert verify_password("NewPass!23", updated.password_hash)


def test_reset_password_missing_user_raises(db):
    with pytest.raises(NotFoundError):
        reset_password(db, 9999, "NewPass!23")
