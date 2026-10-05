"""docker-entrypoint.py 的行为测试。

本机没有 Docker，镜像构建无法实测，所以这里用 mock 把入口脚本的决策路径
钉死：谁在什么时候 chown、降权顺序对不对、失败时会不会把容器卡死。

Run:  python tests/test_docker_entrypoint.py   (pytest-compatible)
"""
import importlib.util
import os
import sys
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "docker_entrypoint", os.path.join(ROOT, "docker-entrypoint.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY = _load_entrypoint()


class _FakeStat:
    def __init__(self, uid, gid):
        self.st_uid = uid
        self.st_gid = gid


def _patch_posix(calls, *, euid=0, owners=None, chown_error=None, walk=None):
    """Windows 上没有 geteuid/setuid/chown，create=True 把它们补出来。

    `owners` 是 {路径: (uid, gid)}，用来模拟数据卷里已经存在的属主。
    `walk` 是 os.walk 的假返回值 —— 默认给一棵合成目录树，这样测试不依赖
    真实文件系统，也不会在磁盘上留下临时文件。
    """
    owners = owners or {}
    walk = walk if walk is not None else (("/data", ["sub"], ["a.db"]),)

    def fake_lstat(path):
        return _FakeStat(*owners.get(path, (0, 0)))

    def fake_chown(path, uid, gid, follow_symlinks=True):
        calls.append(("chown", path, uid, gid))
        if chown_error is not None:
            raise chown_error

    return [
        mock.patch.object(os, "geteuid", lambda: euid, create=True),
        mock.patch.object(os, "lstat", fake_lstat),
        mock.patch.object(os, "chown", fake_chown, create=True),
        mock.patch.object(os, "walk", lambda p: list(walk)),
        mock.patch.object(os, "setgroups", lambda groups: calls.append(("setgroups", tuple(groups))), create=True),
        mock.patch.object(os, "setgid", lambda gid: calls.append(("setgid", gid)), create=True),
        mock.patch.object(os, "setuid", lambda uid: calls.append(("setuid", uid)), create=True),
        mock.patch.object(os, "execvp", lambda f, a: calls.append(("execvp", f, tuple(a))), create=True),
        mock.patch.object(os, "makedirs", lambda *a, **k: calls.append(("makedirs", a[0]))),
    ]


def _run(calls, argv, **kwargs):
    patches = _patch_posix(calls, **kwargs)
    for p in patches:
        p.start()
    try:
        return ENTRY.main(argv)
    finally:
        for p in reversed(patches):
            p.stop()


def test_truthy():
    assert ENTRY._truthy("1")
    assert ENTRY._truthy("TRUE")
    assert ENTRY._truthy(" yes ")
    assert not ENTRY._truthy("")
    assert not ENTRY._truthy(None)
    assert not ENTRY._truthy("0")


def test_data_dir_follows_db_path():
    with mock.patch.dict(os.environ, {"DB_PATH": "/srv/vol/deeperseeker.db"}):
        assert ENTRY.data_dir() == os.path.abspath("/srv/vol")
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DB_PATH", None)
        assert ENTRY.data_dir() == os.path.abspath("/app/data")


def test_default_command_is_app_py():
    calls = []
    _run(calls, [])
    assert ("execvp", "python", ("python", "app.py")) in calls


def test_non_root_skips_chown_and_execs():
    """`docker run --user 10001` 或镜像被改成 USER appuser 时，进程没有改属主
    的权限，必须直接 exec，而不是先报错。"""
    calls = []
    _run(calls, ["python", "app.py"], euid=10001)
    assert calls == [("execvp", "python", ("python", "app.py"))], calls


def test_root_fixes_ownership_then_drops_privileges():
    calls = []
    _run(calls, ["python", "app.py"], euid=0)
    kinds = [c[0] for c in calls]
    # 关键顺序：先改属主，再 setgroups -> setgid -> setuid，最后 exec。
    assert kinds.index("chown") < kinds.index("setgroups") < kinds.index("setgid")
    assert kinds.index("setgid") < kinds.index("setuid") < kinds.index("execvp")
    assert ("setuid", 10001) in calls and ("setgid", 10001) in calls
    assert calls[-1] == ("execvp", "python", ("python", "app.py"))


def test_skip_chown_env_skips_the_walk_but_still_drops_privileges():
    calls = []
    with mock.patch.dict(os.environ, {"DEEPSEEKER_SKIP_CHOWN": "1"}):
        _run(calls, ["python", "app.py"], euid=0)
    assert not [c for c in calls if c[0] == "chown"], calls
    assert ("setuid", 10001) in calls


def test_chown_failure_does_not_brick_the_container():
    """只读挂载下 chown 必然失败；此时应用自己会给出更准确的错误，入口脚本
    不应该把容器停在一条与真实原因无关的日志上。"""
    calls = []
    _run(calls, ["python", "app.py"], euid=0, chown_error=PermissionError("read-only file system"))
    assert calls[-1] == ("execvp", "python", ("python", "app.py"))


def test_ownership_skips_entries_that_already_match():
    calls = []
    owners = {"/data": (10001, 10001), os.path.join("/data", "a.db"): (10001, 10001),
              os.path.join("/data", "sub"): (0, 0)}
    _run(calls, ["python", "app.py"], euid=0, owners=owners)
    chowned = {c[1] for c in calls if c[0] == "chown"}
    assert chowned == {os.path.join("/data", "sub")}, chowned


def test_ownership_fixes_the_data_dir_itself():
    calls = []
    _run(calls, ["python", "app.py"], euid=0)
    chowned = {c[1] for c in calls if c[0] == "chown"}
    assert chowned == {"/data", os.path.join("/data", "a.db"), os.path.join("/data", "sub")}, chowned
    assert all(c[2] == 10001 and c[3] == 10001 for c in calls if c[0] == "chown")


def test_custom_uid_env_is_honoured():
    calls = []
    with mock.patch.dict(os.environ, {"DEEPSEEKER_APP_UID": "1234", "DEEPSEEKER_APP_GID": "5678"}):
        module = _load_entrypoint()
        calls = []
        patches = _patch_posix(calls, euid=0)
        for p in patches:
            p.start()
        try:
            module.main(["python", "app.py"])
        finally:
            for p in reversed(patches):
                p.stop()
    assert ("setuid", 1234) in calls and ("setgid", 5678) in calls


def test_dockerfile_wires_the_entrypoint_and_does_not_hardcode_user():
    """入口脚本只有在 Dockerfile 真的引用它、并且没有提前 USER 切走时才生效。"""
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert 'ENTRYPOINT ["python", "/app/docker-entrypoint.py"]' in dockerfile
    assert "docker-entrypoint.py" in dockerfile
    active = [ln.strip() for ln in dockerfile.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert not any(ln.startswith("USER ") for ln in active), active
    assert 'CMD ["python", "app.py"]' in dockerfile
    assert os.path.exists(os.path.join(ROOT, "docker-entrypoint.py"))


def test_compose_no_longer_asks_for_a_manual_chown():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
        compose = fh.read()
    assert "chown -R 10001:10001" not in compose
    assert "no-new-privileges:true" in compose


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
