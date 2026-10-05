"""外部反代终止 TLS 时的 HTTPS 入口展示（DEEPSEEKER_PUBLIC_HTTPS_URL）。

需求背景：TLS 常常不在这个容器里做 —— 前面是 nginx / fnOS 的 https_ssl 面板，
容器只监听明文端口。这时候控制台的「API 接入信息」只给出 http:// 地址，
Claude Desktop 这类只接受 HTTPS 的客户端根本没法照着填，页面上看不到任何
https 入口。

修法不是去打开 DEEPSEEKER_HTTPS_ENABLED（那会另起一个自签监听、多一张不受信
的证书），而是用 DEEPSEEKER_PUBLIC_HTTPS_URL 把真实的 HTTPS 入口告诉控制台。

这个文件钉住四条契约：
  1. 只接受 https://，其余一律忽略（绝不在 HTTPS 区块里展示明文地址）
  2. 尾部斜杠被规范化掉，拼 /v1 时不会出现 `//v1`
  3. 没配置时整个区块不渲染（不产生空标题）
  4. 与「容器自身监听」的区块互不干扰，可同时存在

Run:  python tests/test_public_https.py   (pytest-compatible)
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import functions  # noqa: E402

# 跑在临时库上，别碰仓库里的 deeperseeker.db（dashboard() 会读 users 表）。
_TMPDIR = tempfile.mkdtemp(prefix="deeperseeker-public-https-test-")
functions._db = os.path.join(_TMPDIR, "test.db")
functions.init_db()

PUBLIC = "https://192.168.5.3:14000"


def _client(app_module, sid):
    from fastapi.testclient import TestClient

    app_module.SESSIONS[sid] = time.time()
    app_module.SESSION_USERS[sid] = app_module.ADMIN_USER
    client = TestClient(app_module.app)
    client.cookies.set("session_id", sid)
    return client


def _drop(app_module, sid):
    app_module.SESSIONS.pop(sid, None)
    app_module.SESSION_USERS.pop(sid, None)


class _patched:
    """临时改 app 模块上的配置常量，退出时还原。"""

    def __init__(self, app_module, **values):
        self.app = app_module
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = getattr(self.app, key)
            setattr(self.app, key, value)
        return self.app

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(self.app, key, value)
        return False


# --------------------------------------------------------------- 规范化


def test_normalize_accepts_a_plain_https_url():
    import app as app_module

    assert app_module._normalize_public_https_url(PUBLIC) == PUBLIC
    assert app_module._normalize_public_https_url("https://nas.local") == "https://nas.local"


def test_normalize_strips_whitespace_and_trailing_slashes():
    """不裁掉尾部斜杠的话，页面上会出现 https://host:14000//v1。"""
    import app as app_module

    normalize = app_module._normalize_public_https_url
    assert normalize("  " + PUBLIC + "  ") == PUBLIC
    assert normalize(PUBLIC + "/") == PUBLIC
    assert normalize(PUBLIC + "///") == PUBLIC


def test_normalize_rejects_anything_that_is_not_https():
    import app as app_module

    normalize = app_module._normalize_public_https_url
    for bad in ["", "   ", None, "http://192.168.5.3:14000", "192.168.5.3:14000",
                "//192.168.5.3:14000", "ftp://x", "https:/192.168.5.3"]:
        assert normalize(bad) == "", bad


def test_normalize_keeps_the_path_when_there_is_one():
    """反代有时挂在子路径下（https://host/proxy），只裁尾部斜杠。"""
    import app as app_module

    assert app_module._normalize_public_https_url("https://host/proxy/") == "https://host/proxy"


# --------------------------------------------------------------- 信息组装


def test_public_https_info_is_none_when_unset():
    import app as app_module

    with _patched(app_module, PUBLIC_HTTPS_URL=""):
        assert app_module._public_https_info() is None


def test_public_https_info_shape():
    import app as app_module

    with _patched(app_module, PUBLIC_HTTPS_URL=PUBLIC, PUBLIC_HTTPS_NOTE=""):
        info = app_module._public_https_info()
    assert info["url"] == PUBLIC
    assert info["openai_base"] == PUBLIC + "/v1"
    assert info["anthropic_base"] == PUBLIC
    assert info["note"] is None


def test_public_https_info_carries_the_custom_note():
    import app as app_module

    with _patched(app_module, PUBLIC_HTTPS_URL=PUBLIC, PUBLIC_HTTPS_NOTE="证书由面板签发"):
        info = app_module._public_https_info()
    assert info["note"] == "证书由面板签发"


def test_openai_base_never_has_a_double_slash():
    """端到端地确认：把带尾斜杠的值规范化后再拼 /v1，不会出现 //v1。"""
    import app as app_module

    normalized = app_module._normalize_public_https_url(PUBLIC + "/")
    with _patched(app_module, PUBLIC_HTTPS_URL=normalized):
        info = app_module._public_https_info()
    assert "//v1" not in info["openai_base"]
    assert info["openai_base"] == PUBLIC + "/v1"


# --------------------------------------------------------------- 页面渲染


def test_dashboard_shows_the_https_entry_while_the_container_has_no_tls():
    """这就是用户遇到的场景：容器没开 HTTPS，但反代那侧有。"""
    import app as app_module

    sid = "test-session-public-https"
    client = _client(app_module, sid)
    try:
        with _patched(app_module, PUBLIC_HTTPS_URL=PUBLIC,
                      PUBLIC_HTTPS_NOTE="由 NAS 上的 https_ssl 面板终止 TLS",
                      HTTPS_ENABLED=False):
            body = client.get("/dashboard").text
        assert "HTTPS 接入" in body
        assert PUBLIC in body
        assert PUBLIC + "/v1" in body
        assert "由 NAS 上的 https_ssl 面板终止 TLS" in body
        assert "只接受 HTTPS 的客户端" in body
    finally:
        _drop(app_module, sid)


def test_dashboard_hides_the_block_when_nothing_is_configured():
    """没配就整块不渲染 —— 不能留一个空标题。"""
    import app as app_module

    sid = "test-session-public-https-off"
    client = _client(app_module, sid)
    try:
        with _patched(app_module, PUBLIC_HTTPS_URL="", HTTPS_ENABLED=False):
            body = client.get("/dashboard").text
        assert "HTTPS 接入" not in body
    finally:
        _drop(app_module, sid)


def test_dashboard_lists_both_https_base_urls():
    """OpenAI 与 Anthropic 两个兼容路径都要给出来，省得用户自己拼。"""
    import app as app_module

    sid = "test-session-public-https-both"
    client = _client(app_module, sid)
    try:
        with _patched(app_module, PUBLIC_HTTPS_URL=PUBLIC, PUBLIC_HTTPS_NOTE=""):
            body = client.get("/dashboard").text
        assert 'data-copy="' + PUBLIC + '/v1"' in body
        assert 'data-copy="' + PUBLIC + '"' in body
    finally:
        _drop(app_module, sid)


def test_plaintext_public_url_is_never_rendered():
    """http:// 的值在规范化阶段就被丢掉，页面里不可能出现。"""
    import app as app_module

    sid = "test-session-public-https-http"
    client = _client(app_module, sid)
    try:
        rejected = app_module._normalize_public_https_url("http://192.168.5.3:14000")
        with _patched(app_module, PUBLIC_HTTPS_URL=rejected, HTTPS_ENABLED=False):
            body = client.get("/dashboard").text
        assert rejected == ""
        assert "HTTPS 接入" not in body
        assert "http://192.168.5.3:14000" not in body
    finally:
        _drop(app_module, sid)


def test_both_https_sources_can_coexist_and_are_labelled():
    """容器自己监听 + 外部反代同时存在时，标题要能区分，别让人以为是同一个。"""
    import app as app_module

    fake_native = {
        "url": "https://192.168.5.3:4443", "port": 4443, "cert": "/app/data/tls/x.crt",
        "self_signed": True, "san": ["192.168.5.3"], "expires": "2036-09-13",
        "only": False, "error": None,
    }
    sid = "test-session-public-https-coexist"
    client = _client(app_module, sid)
    try:
        saved = app_module._https_display_info
        app_module._https_display_info = lambda request: fake_native
        try:
            with _patched(app_module, PUBLIC_HTTPS_URL=PUBLIC, PUBLIC_HTTPS_NOTE=""):
                body = client.get("/dashboard").text
        finally:
            app_module._https_display_info = saved
        assert "HTTPS 接入（容器自身监听）" in body
        assert "https://192.168.5.3:4443" in body
        assert PUBLIC in body
    finally:
        _drop(app_module, sid)


def test_native_heading_has_no_suffix_when_there_is_no_proxy_entry():
    """只有容器自监听时，标题保持原样，不要多一个没意义的括号。"""
    import app as app_module

    fake_native = {
        "url": "https://192.168.5.3:4443", "port": 4443, "cert": "/app/data/tls/x.crt",
        "self_signed": True, "san": [], "expires": "2036-09-13",
        "only": False, "error": None,
    }
    sid = "test-session-public-https-native-only"
    client = _client(app_module, sid)
    try:
        saved = app_module._https_display_info
        app_module._https_display_info = lambda request: fake_native
        try:
            with _patched(app_module, PUBLIC_HTTPS_URL=""):
                body = client.get("/dashboard").text
        finally:
            app_module._https_display_info = saved
        assert "HTTPS 接入" in body
        assert "（容器自身监听）" not in body
    finally:
        _drop(app_module, sid)


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
