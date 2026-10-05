"""控制台多用户账号（管理员添加账号）。

需求背景：登录页原本有一行「账号由 .env 中的 DEEPSEEKER_ADMIN_USER 与
DEEPSEEKER_ADMIN_PASSWORD 配置」的提示，且没有任何加账号的入口。现在：

  * 登录页**不再暴露** .env 变量名，也**不提供自助注册**；
  * 账号一律由已登录的管理员在控制台「账号管理」里添加；
  * 新增账号与 .env 内置管理员**权限完全相同**；
  * 内置管理员不写进 users 表 —— 数据库被清空也锁不住自己。

这个文件把上面四条钉成可回归的契约。分三层：

  1. 纯函数层 —— scrypt 哈希 / 校验规则（不碰数据库）
  2. 数据层   —— users 表 CRUD（跑在临时库上，不污染真库）
  3. 应用层   —— _authenticate / 路由 / 模板渲染（TestClient 走真 app）

每个用例都自己造数据、只用自己那份，因此 **与执行顺序无关**
（本文件自带的 main() 是按名字排序跑的，跨用例共享状态会随机炸）。

Run:  python tests/test_users.py   (pytest-compatible)
"""
import os
import sys
import tempfile
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import functions  # noqa: E402

# 整个文件跑在临时库上：绝不能让测试写到真的 deeperseeker.db。
# get_db() 在调用时才读 functions._db，所以在这里改一次就够了，
# 之后 import app 也一样生效。
_TMPDIR = tempfile.mkdtemp(prefix="deeperseeker-users-test-")
functions._db = os.path.join(_TMPDIR, "test.db")
functions.init_db()


def _ensure_user(username, password):
    """幂等地准备一个账号（重跑本文件时不会因为重名而炸）。"""
    row = functions.get_user(username)
    if row is None:
        functions.create_user(username, password)
        row = functions.get_user(username)
    return row


def _clear_last_login(username):
    """把最近登录时间清空，用来验证「登录成功才写这一列」。"""
    conn = functions.get_db()
    try:
        conn.execute("UPDATE users SET last_login = NULL WHERE username = ?", (username,))
        conn.commit()
    finally:
        conn.close()


def _reset_login_limiter():
    """登录失败限流是模块级状态，测试之间必须清零，否则互相污染。"""
    import app as app_module

    app_module._login_fails["count"] = 0
    app_module._login_fails["locked_until"] = 0


def _login_as_builtin(app_module, sid):
    """伪造一个已登录的内置管理员会话（跳过表单与限流）。"""
    app_module.SESSIONS[sid] = time.time()
    app_module.SESSION_USERS[sid] = app_module.ADMIN_USER
    return sid


def _admin_client(app_module, sid):
    """返回一个带着管理员会话 cookie 的 TestClient。"""
    from fastapi.testclient import TestClient

    client = TestClient(app_module.app)
    client.cookies.set("session_id", _login_as_builtin(app_module, sid))
    return client


def _drop_session(app_module, sid):
    app_module.SESSIONS.pop(sid, None)
    app_module.SESSION_USERS.pop(sid, None)


# ------------------------------------------------------------------ 1. 纯函数层


def test_hash_password_is_salted_and_hides_the_plaintext():
    h1 = functions.hash_password("hunter2")
    h2 = functions.hash_password("hunter2")
    assert h1 != h2, "两次哈希必须不同（盐随机），否则相同密码可被一眼识别"
    assert "hunter2" not in h1
    assert h1.startswith("scrypt$")
    assert len(h1.split("$")) == 6


def test_hash_password_embeds_its_parameters():
    """参数随哈希一起存，以后调 n/r/p 不影响老密码校验。"""
    scheme, n, r, p, salt_hex, hash_hex = functions.hash_password("whatever").split("$")
    assert scheme == "scrypt"
    assert int(n) == functions._SCRYPT_N
    assert int(r) == functions._SCRYPT_R
    assert int(p) == functions._SCRYPT_P
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(hash_hex)) == functions._SCRYPT_DKLEN


def test_verify_password_round_trip():
    h = functions.hash_password("correct horse")
    assert functions.verify_password("correct horse", h) is True
    assert functions.verify_password("correct  horse", h) is False
    assert functions.verify_password("", h) is False
    assert functions.verify_password(None, h) is False


def test_verify_password_survives_garbage_stored_values():
    """库里出现脏数据只能是校验失败，绝不能抛异常把登录接口打成 500。"""
    for bad in ["", None, "garbage", "scrypt$1", "md5$1$1$1$aa$bb", "scrypt$a$b$c$zz$yy"]:
        assert functions.verify_password("hunter2", bad) is False, bad


def test_verify_password_rejects_a_tampered_hash():
    h = functions.hash_password("hunter2")
    head, tail = h.rsplit("$", 1)
    flipped = "0" if tail[-1] != "0" else "1"
    assert functions.verify_password("hunter2", f"{head}${tail[:-1]}{flipped}") is False


def test_validate_username_rules():
    assert functions.validate_username("alice") == (True, "alice")
    assert functions.validate_username("  bob  ") == (True, "bob"), "首尾空白应被裁掉"
    assert functions.validate_username("ok-name_1.2") == (True, "ok-name_1.2")
    assert functions.validate_username("a" * functions.USERNAME_MAX_LEN)[0] is True
    for bad in ["", "   ", None, "a" * (functions.USERNAME_MAX_LEN + 1),
                "bad name", "bad/name", "user@host", "用户名", "a\nb", "a\tb"]:
        ok, msg = functions.validate_username(bad)
        assert ok is False, bad
        assert msg, "拒绝时必须给出可展示的原因"


def test_validate_password_rules():
    assert functions.validate_password("123456") == (True, "123456")
    assert functions.validate_password("x" * functions.PASSWORD_MAX_LEN)[0] is True
    for bad in ["", None, "12345", "x" * (functions.PASSWORD_MAX_LEN + 1)]:
        ok, msg = functions.validate_password(bad)
        assert ok is False, bad
        assert msg


# ------------------------------------------------------------------ 2. 数据层


def test_create_user_stores_a_hash_not_the_password():
    name = functions.create_user("hash-probe", "probe-secret", created_by="root")
    assert name == "hash-probe"
    row = functions.get_user("hash-probe")
    assert row is not None
    assert row["password_hash"].startswith("scrypt$")
    assert "probe-secret" not in row["password_hash"]
    assert functions.verify_password("probe-secret", row["password_hash"]) is True
    assert row["created_by"] == "root"
    assert row["created_at"] > 0
    assert row["last_login"] is None
    assert "hash-probe" in [u["username"] for u in functions.list_users()]


def test_create_user_rejects_a_duplicate():
    _ensure_user("dup-probe", "dup-secret")
    before = functions.count_users()
    try:
        functions.create_user("dup-probe", "another-secret")
    except ValueError as exc:
        assert "已存在" in str(exc)
    else:
        raise AssertionError("重名必须抛 ValueError")
    assert functions.count_users() == before


def test_create_user_rejects_bad_input_without_touching_the_table():
    before = functions.count_users()
    for bad_name, bad_pw in [("bad name", "123456"), ("badinput-probe", "123")]:
        try:
            functions.create_user(bad_name, bad_pw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"应拒绝 {bad_name!r}/{bad_pw!r}")
    assert functions.count_users() == before
    assert functions.get_user("badinput-probe") is None


def test_usernames_are_case_sensitive():
    """SQLite 的 UNIQUE 对 TEXT 默认区分大小写 —— 钉住这个行为，
    免得有人以为「Case」会被当成内置管理员「case」而被挡下。"""
    lower = _ensure_user("case-probe", "case-lower-pw")
    if functions.get_user("Case-Probe") is None:
        functions.create_user("Case-Probe", "case-upper-pw")
    upper = functions.get_user("Case-Probe")
    assert upper["id"] != lower["id"]
    assert functions.verify_password("case-lower-pw", functions.get_user("case-probe")["password_hash"])
    assert functions.verify_password("case-upper-pw", upper["password_hash"])


def test_set_user_password():
    uid = _ensure_user("pw-probe", "pw-probe-old")["id"]
    assert functions.set_user_password(uid, "pw-probe-new") is True
    assert functions.verify_password("pw-probe-new", functions.get_user("pw-probe")["password_hash"])
    assert not functions.verify_password("pw-probe-old", functions.get_user("pw-probe")["password_hash"])
    assert functions.set_user_password(999999, "pw-probe-new") is False
    try:
        functions.set_user_password(uid, "123")
    except ValueError:
        pass
    else:
        raise AssertionError("过短的密码必须抛 ValueError")


def test_touch_user_login_records_the_timestamp():
    _ensure_user("touch-probe", "touch-secret")
    functions.touch_user_login("touch-probe")
    assert functions.get_user("touch-probe")["last_login"] > 0
    functions.touch_user_login("nobody-here")  # 不存在的账号不能抛异常


def test_delete_user():
    uid = _ensure_user("delete-probe", "delete-secret")["id"]
    assert functions.delete_user(uid) is True
    assert functions.get_user("delete-probe") is None
    assert functions.delete_user(uid) is False, "重复删除应返回 False 而不是报错"


def test_init_db_is_idempotent():
    _ensure_user("idem-probe", "idem-secret")
    before = functions.count_users()
    functions.init_db()
    functions.init_db()
    assert functions.count_users() == before
    assert functions.get_user("idem-probe") is not None


def test_builtin_admin_is_never_stored_in_the_users_table():
    """这是「数据库丢了也不会被锁在门外」的实现方式。"""
    import app as app_module

    assert app_module.ADMIN_USER not in [u["username"] for u in functions.list_users()]


# ------------------------------------------------------------------ 3. 应用层


def test_authenticate_accepts_the_builtin_admin():
    import app as app_module

    assert app_module._authenticate(
        app_module.ADMIN_USER, app_module.ADMIN_PASSWORD
    ) == app_module.ADMIN_USER
    assert app_module._authenticate(app_module.ADMIN_USER, "definitely-wrong") is None
    assert app_module._authenticate("", "") is None
    assert app_module._authenticate(None, None) is None


def test_authenticate_accepts_a_table_account():
    import app as app_module

    _ensure_user("auth-probe", "auth-secret")
    _clear_last_login("auth-probe")
    assert app_module._authenticate("auth-probe", "auth-secret") == "auth-probe"
    assert functions.get_user("auth-probe")["last_login"] > 0, "登录成功应记录最近登录时间"
    assert app_module._authenticate("auth-probe", "nope") is None
    assert app_module._authenticate("nobody", "whatever") is None


def test_login_page_no_longer_leaks_the_env_variable_names():
    from fastapi.testclient import TestClient

    import app as app_module

    response = TestClient(app_module.app).get("/login")
    assert response.status_code == 200
    body = response.text
    assert "DEEPSEEKER_ADMIN_USER" not in body
    assert "DEEPSEEKER_ADMIN_PASSWORD" not in body
    assert "登录控制台" in body


def test_login_page_offers_no_self_service_registration():
    from fastapi.testclient import TestClient

    import app as app_module

    body = TestClient(app_module.app).get("/login").text
    assert "注册" not in body
    assert "/register" not in body


def test_login_flow_with_an_added_account():
    from fastapi.testclient import TestClient

    import app as app_module

    _ensure_user("login-probe", "login-secret")
    _reset_login_limiter()
    client = TestClient(app_module.app)
    response = client.post("/login", data={"username": "login-probe", "password": "login-secret"})
    assert response.status_code == 200, response.text
    sid = response.cookies.get("session_id")
    assert sid and sid in app_module.SESSIONS
    assert app_module.SESSION_USERS[sid] == "login-probe", "会话必须记住登录的是哪个账号"
    try:
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert "账号管理" in page.text
        assert "login-probe" in page.text
    finally:
        _drop_session(app_module, sid)


def test_login_still_rejects_a_wrong_password():
    from fastapi.testclient import TestClient

    import app as app_module

    _ensure_user("login-probe", "login-secret")
    _reset_login_limiter()
    try:
        response = TestClient(app_module.app).post(
            "/login", data={"username": "login-probe", "password": "wrong"}
        )
        assert response.status_code == 200
        assert "用户名或密码错误" in response.text
        assert not response.cookies.get("session_id")
    finally:
        _reset_login_limiter()


def test_logout_forgets_both_the_session_and_its_user():
    import app as app_module

    sid = "test-session-users-logout"
    client = _admin_client(app_module, sid)
    client.get("/logout")
    assert sid not in app_module.SESSIONS
    assert sid not in app_module.SESSION_USERS


def test_users_add_round_trip():
    import app as app_module

    functions.delete_user(_ensure_user("route-probe", "route-secret")["id"])
    client = _admin_client(app_module, "test-session-users-add")
    sid = "test-session-users-add"
    try:
        response = client.post(
            "/users/add",
            data={"username": "route-probe", "password": "route-secret",
                  "password2": "route-secret"},
        )
        assert response.status_code == 200
        assert "/dashboard?user_added=1" in response.text
        row = functions.get_user("route-probe")
        assert row is not None
        assert row["created_by"] == app_module.ADMIN_USER
        assert functions.verify_password("route-secret", row["password_hash"]) is True

        # 改密码
        changed = client.post(
            f"/users/{row['id']}/password",
            data={"password": "route-new-pw", "password2": "route-new-pw"},
        )
        assert "/dashboard?user_pw=1" in changed.text
        assert functions.verify_password(
            "route-new-pw", functions.get_user("route-probe")["password_hash"]
        )

        # 删账号
        removed = client.post(f"/users/{row['id']}/delete")
        assert "/dashboard?user_deleted=1" in removed.text
        assert functions.get_user("route-probe") is None
    finally:
        _drop_session(app_module, sid)


def test_user_routes_require_a_session():
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    before = functions.count_users()
    for path, payload in [
        ("/users/add", {"username": "nosession-probe", "password": "nosession-secret",
                        "password2": "nosession-secret"}),
        ("/users/1/delete", None),
        ("/users/1/password", {"password": "nosession-secret", "password2": "nosession-secret"}),
    ]:
        response = client.post(path, data=payload) if payload else client.post(path)
        assert response.status_code == 200
        assert "url=/login" in response.text, (path, response.text)
    assert functions.count_users() == before
    assert functions.get_user("nosession-probe") is None


def test_users_add_rejects_mismatched_passwords():
    import app as app_module

    sid = "test-session-users-mismatch"
    client = _admin_client(app_module, sid)
    try:
        response = client.post(
            "/users/add",
            data={"username": "mismatch-probe", "password": "mismatch-secret",
                  "password2": "mismatch-secreT"},
        )
        assert "user_err=" in response.text
        assert quote("两次输入的密码不一致。") in response.text
        assert functions.get_user("mismatch-probe") is None
    finally:
        _drop_session(app_module, sid)


def test_users_add_refuses_to_shadow_the_builtin_admin():
    import app as app_module

    sid = "test-session-users-shadow"
    client = _admin_client(app_module, sid)
    try:
        response = client.post(
            "/users/add",
            data={"username": app_module.ADMIN_USER, "password": "shadow-secret",
                  "password2": "shadow-secret"},
        )
        assert "user_err=" in response.text
        assert quote("请换一个用户名") in response.text
        assert functions.get_user(app_module.ADMIN_USER) is None
    finally:
        _drop_session(app_module, sid)


def test_users_add_surfaces_duplicates_as_a_flash_error():
    import app as app_module

    _ensure_user("dup-route-probe", "dup-route-secret")
    sid = "test-session-users-dup"
    client = _admin_client(app_module, sid)
    try:
        response = client.post(
            "/users/add",
            data={"username": "dup-route-probe", "password": "another-secret",
                  "password2": "another-secret"},
        )
        assert "user_err=" in response.text
        assert quote("已存在") in response.text
    finally:
        _drop_session(app_module, sid)


def test_users_add_rejects_a_short_password_with_a_friendly_message():
    import app as app_module

    sid = "test-session-users-shortpw"
    client = _admin_client(app_module, sid)
    try:
        response = client.post(
            "/users/add",
            data={"username": "shortpw-probe", "password": "123", "password2": "123"},
        )
        assert "user_err=" in response.text
        assert quote(f"密码至少 {functions.PASSWORD_MIN_LEN} 位。") in response.text
        assert functions.get_user("shortpw-probe") is None
    finally:
        _drop_session(app_module, sid)


def test_dashboard_lists_accounts_and_offers_the_add_form():
    import app as app_module

    _ensure_user("dash-probe", "dash-secret")
    sid = "test-session-users-dash"
    client = _admin_client(app_module, sid)
    try:
        page = client.get("/dashboard")
        assert page.status_code == 200
        body = page.text
        assert "账号管理" in body
        assert app_module.ADMIN_USER in body, "内置管理员必须出现在账号表里"
        assert ".env 内置" in body
        assert "dash-probe" in body
        assert "控制台添加" in body
        assert 'action="/users/add"' in body
        assert 'value="/users/' in body, "重置密码的下拉框应带上各账号的路由"
    finally:
        _drop_session(app_module, sid)


def test_dashboard_marks_the_logged_in_account():
    import app as app_module

    _ensure_user("badge-probe", "badge-secret")
    sid = "test-session-users-badge"
    app_module.SESSIONS[sid] = time.time()
    app_module.SESSION_USERS[sid] = "badge-probe"
    try:
        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        client.cookies.set("session_id", sid)
        body = client.get("/dashboard").text
        assert "当前登录" in body
        assert "badge-probe" in body
    finally:
        _drop_session(app_module, sid)


def test_dashboard_flash_style_depends_on_success_or_error():
    import app as app_module

    sid = "test-session-users-flash"
    client = _admin_client(app_module, sid)
    try:
        ok = client.get("/dashboard?user_added=1&name=ivan").text
        assert '<div class="notice">账号「ivan」已创建。</div>' in ok
        bad = client.get(f"/dashboard?user_err={quote('用户名不能为空。')}").text
        assert '<div class="warning">用户名不能为空。</div>' in bad
    finally:
        _drop_session(app_module, sid)


def test_pruning_drops_the_user_mapping_too():
    """会话过期清理必须把两个字典一起清 —— 只清 SESSIONS 会留下一个
    永不回收的 SESSION_USERS，而且 sid 复用时可能串号。"""
    import app as app_module

    stale = "test-session-users-stale"
    app_module.SESSIONS[stale] = time.time() - app_module.SESSION_TTL - 60
    app_module.SESSION_USERS[stale] = "badge-probe"
    app_module._prune_admin_sessions()
    assert stale not in app_module.SESSIONS
    assert stale not in app_module.SESSION_USERS


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
