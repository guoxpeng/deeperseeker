"""Per-token client-identity profiles (B11, Stage 1 audit).

The Android header set used to be byte-identical for every token. It is now
derived per token from the token itself, so two accounts no longer present the
same device while one account keeps presenting the same device across
restarts. These tests pin down the contract:

  * stable   — same token -> same identity, on every call and every process
  * spread   — different tokens -> different identities (most of the time)
  * off      — exact legacy header set, no rotation at all
  * device   — UA + timezone rotate, version/locale stay pinned (safe default)
  * full     — version and locale rotate too
  * seed     — changing DEEPSEEKER_IDENTITY_SEED re-shuffles the assignment
  * surface  — the dashboard shows each token's identity

Run:  python tests/test_identity_profiles.py   (pytest-compatible)
"""
import os
import sys
import time
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import functions  # noqa: E402

# Deterministic token strings — the identity is a pure function of the token,
# so these always map to the same devices.
TOKENS = [f"identity-probe-token-{i:02d}" for i in range(12)]

LEGACY_UA = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7)"


def _identities(tokens=TOKENS):
    return [functions.resolve_identity(t) for t in tokens]


def test_identity_is_stable_per_token():
    """Same token, repeated calls, identical headers — a fingerprint that
    changes per request is more suspicious than one that never changes."""
    first = functions.get_headers(TOKENS[0])
    for _ in range(5):
        assert functions.get_headers(TOKENS[0]) == first
    assert functions.resolve_identity(TOKENS[0]) == functions.resolve_identity(TOKENS[0])


def test_identity_is_a_pure_function_of_the_token():
    """No per-process state (cache, counter, dict ordering) may leak into the
    derivation — recomputing the digest from scratch must land on the same
    device, in this process and in any other."""
    import hashlib

    token = TOKENS[3]
    expected_stream = hashlib.sha256(
        ("deeperseeker-identity-v1\x00" + functions.IDENTITY_SEED + "\x00" + token).encode("utf-8")
    ).digest()
    assert functions._identity_stream(token) == expected_stream
    expected_ua = functions._USER_AGENT_POOL[expected_stream[0] % len(functions._USER_AGENT_POOL)]
    assert functions.resolve_identity(token)["user_agent"] == expected_ua


def test_device_mode_spreads_user_agents():
    """Default mode: distinct tokens should mostly land on distinct devices."""
    agents = {ident["user_agent"] for ident in _identities(TOKENS[:8])}
    assert len(agents) >= 5, agents
    assert all(a.startswith("Dalvik/2.1.0 (Linux; U; Android ") for a in agents), agents


def test_device_mode_keeps_semantic_fields_pinned():
    """Version and locale are read upstream, so `device` mode must not touch
    them — that is the whole point of the conservative default."""
    for ident in _identities():
        assert ident["client_version"] == functions.DEEPSEEKER_CLIENT_VERSION
        assert ident["locale"] == "en_US"
        assert ident["accept_language"] == "en-US,en;q=0.9"


def test_off_mode_restores_the_legacy_header_set():
    """DEEPSEEKER_IDENTITY_ROTATION=off must reproduce the pre-B11 headers
    exactly, so a regression can always be bisected back to known-good."""
    with mock.patch.object(functions, "IDENTITY_ROTATION", "off"):
        for token in TOKENS[:4]:
            headers = functions.get_headers(token)
            assert headers["user-agent"] == LEGACY_UA
            assert headers["x-client-version"] == functions.DEEPSEEKER_CLIENT_VERSION
            assert headers["x-client-locale"] == "en_US"
            assert headers["accept-language"] == "en-US,en;q=0.9"
            assert headers["x-client-timezone-offset"] == functions._TZ_OFFSET_POOL[0]


def test_off_mode_ignores_the_token():
    """`off` means off: every token gets the identical header set."""
    with mock.patch.object(functions, "IDENTITY_ROTATION", "off"):
        headers = [functions.get_headers(t) for t in TOKENS[:5]]
    ua = {h["user-agent"] for h in headers}
    assert ua == {LEGACY_UA}, ua


def test_empty_token_falls_back_to_the_legacy_identity():
    """No token means no identity to derive from — never crash, never emit a
    half-built header set."""
    identity = functions.resolve_identity("")
    assert identity["user_agent"] == LEGACY_UA
    assert identity["client_version"] == functions.DEEPSEEKER_CLIENT_VERSION


def test_full_mode_rotates_version_and_locale():
    with mock.patch.object(functions, "IDENTITY_ROTATION", "full"):
        identities = _identities(TOKENS)
    versions = {i["client_version"] for i in identities}
    locales = {i["locale"] for i in identities}
    assert versions <= set(functions._CLIENT_VERSION_POOL), versions
    assert len(versions) >= 2, versions
    assert len(locales) >= 2, locales
    # Accept-Language must always match the advertised locale — a mismatch is
    # itself a fingerprint.
    for ident in identities:
        assert ident["accept_language"].startswith(ident["locale"].replace("_", "-")), ident


def test_identity_seed_reshuffles_the_assignment():
    """Two operators seeing the same collision can change the seed to get a
    different, still-deterministic mapping."""
    before = [i["user_agent"] for i in _identities(TOKENS[:8])]
    with mock.patch.object(functions, "IDENTITY_SEED", "another-salt"):
        after = [i["user_agent"] for i in _identities(TOKENS[:8])]
    assert before != after
    with mock.patch.object(functions, "IDENTITY_SEED", "another-salt"):
        assert after == [i["user_agent"] for i in _identities(TOKENS[:8])]


def test_digest_never_contains_the_raw_token():
    """The stream is a one-way digest, and it is domain-separated so it can
    never be confused with a hash of the token computed elsewhere."""
    token = TOKENS[0]
    stream = functions._identity_stream(token)
    assert len(stream) == 32
    assert token.encode() not in stream
    assert stream != functions._identity_stream(token + "x")


def test_headers_keep_the_android_bypass_fields():
    """Rotating the descriptive fields must not disturb the fields the WAF
    bypass actually depends on."""
    headers = functions.get_headers(TOKENS[2], pow="pow-blob")
    assert headers["x-client-platform"] == "android"
    assert headers["x-client-bundle-id"] == "com.deepseek.chat"
    assert headers["origin"] == "https://chat.deepseek.com"
    assert headers["referer"] == "https://chat.deepseek.com/"
    assert headers["x-ds-pow-response"] == "pow-blob"
    assert headers["authorization"] == f"Bearer {TOKENS[2]}"


def test_describe_identity_shape():
    described = functions.describe_identity(TOKENS[1])
    assert described["model"]
    assert described["android"].startswith("Android ")
    assert described["timezone"].startswith("UTC")
    assert described["model"] in described["summary"]
    assert described["rotation"] in ("off", "device", "full")


def test_format_tz_offset():
    assert functions.format_tz_offset(0) == "UTC+0"
    assert functions.format_tz_offset(28800) == "UTC+8"
    assert functions.format_tz_offset(-18000) == "UTC-5"
    assert functions.format_tz_offset(19800) == "UTC+5:30"
    assert functions.format_tz_offset("bogus") == "bogus"


def test_concurrency_cap_default_is_conservative():
    """The soft per-token cap drives how hard one account gets hit; 8 allowed
    a small pool to absorb a large burst on a single account."""
    default = max(1, int(os.getenv("DEEPSEEKER_TOKEN_CONCURRENCY", "2")))
    assert default == 2
    assert functions.TOKEN_CONCURRENCY_CAP <= 4, functions.TOKEN_CONCURRENCY_CAP


def test_dashboard_renders_the_identity_column():
    """The rotation has to be visible, otherwise an operator cannot tell a
    collision from a bug."""
    from fastapi.testclient import TestClient

    import app as app_module

    rows = [
        {"id": 1, "alias": "主号", "token": TOKENS[0], "status": "ACTIVE",
         "last_used": None, "rate_limited_until": None},
        {"id": 2, "alias": "备用", "token": TOKENS[1], "status": "RATE_LIMITED",
         "last_used": 1700000000.0, "rate_limited_until": 1700000600.0},
    ]
    stats = {"total": 2, "active": 1, "limited": 1, "sessions": 3, "files": 0}
    sid = "test-session-identity-profiles"
    saved_tokens = app_module.get_tokens
    saved_stats = app_module.get_token_stats
    app_module.get_tokens = lambda: rows
    app_module.get_token_stats = lambda: stats
    app_module.SESSIONS[sid] = time.time()
    try:
        client = TestClient(app_module.app)
        client.cookies.set("session_id", sid)
        response = client.get("/dashboard")
        assert response.status_code == 200, (response.status_code, response.text)
        assert "客户端身份" in response.text
        for row in rows:
            expected = functions.describe_identity(row["token"])["summary"]
            assert expected in response.text, (row["token"], expected)
    finally:
        app_module.get_tokens = saved_tokens
        app_module.get_token_stats = saved_stats
        app_module.SESSIONS.pop(sid, None)


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
