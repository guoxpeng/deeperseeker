#!/usr/bin/env python3
"""容器入口：修正数据卷属主后降权运行。

背景
----
镜像以非 root 用户（uid 10001）运行应用，但命名卷是 Docker 在**首次创建**
时以 root 建立的。于是从旧版本升级、或把已存在的卷挂回来时，非 root 用户
写不进 /app/data，容器直接起不来 —— 用户必须先手动跑一次
``docker run --rm -v <卷>:/data alpine chown -R 10001:10001 /data``。

这里把那次手动操作挪进容器启动流程：以 root 进入 → 修正 /app/data 属主 →
``setuid`` 降权 → exec 真正的命令。用户不需要知道 uid 是多少。

为什么用 Python 而不是 shell
----------------------------
1. ``setpriv`` / ``gosu`` 不保证在 python:*-slim 里存在，装它们要多一层 apt；
   ``os.setgid`` / ``os.setuid`` 是标准库，零依赖。
2. Windows 检出时 shell 脚本可能带上 CRLF，shebang 会失效；Python 不在乎。
3. 用 ``os.execvp`` 替换进程，不产生额外的父进程，信号（SIGTERM）直达应用。

行为
----
* 非 root 启动（如 ``docker run --user 10001``）→ 不尝试 chown，直接 exec。
* ``DEEPSEEKER_SKIP_CHOWN=1`` → 跳过 chown（数据卷只读、或属主已正确时用）。
* chown 失败只告警不中止：只读挂载下应用仍会以自身错误暴露问题，而不是让
  容器停在一条与真实原因无关的入口日志上。
"""
import os
import sys

APP_UID = int(os.environ.get("DEEPSEEKER_APP_UID") or 10001)
APP_GID = int(os.environ.get("DEEPSEEKER_APP_GID") or 10001)

DEFAULT_DB_PATH = "/app/data/deeperseeker.db"


def _truthy(raw):
    return (raw or "").strip().lower() in ("1", "true", "yes", "on", "y")


def data_dir():
    """与 functions.data_dir() 同一口径：DB_PATH 所在目录。"""
    return os.path.dirname(os.path.abspath(os.environ.get("DB_PATH") or DEFAULT_DB_PATH))


def fix_ownership(path):
    """把 path 整棵子树的属主改成运行用户。数据卷里只有 SQLite、API Key 和
    Cookie 文件，递归成本可以忽略。"""
    changed = 0
    failed = []
    for root, dirs, files in os.walk(path):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            try:
                st = os.lstat(name)
            except OSError as exc:
                failed.append(f"{name}: {exc}")
                continue
            if st.st_uid == APP_UID and st.st_gid == APP_GID:
                continue
            try:
                os.chown(name, APP_UID, APP_GID, follow_symlinks=False)
                changed += 1
            except (OSError, NotImplementedError) as exc:
                failed.append(f"{name}: {exc}")
    return changed, failed


def main(argv):
    command = argv or ["python", "app.py"]

    if os.geteuid() != 0:
        # 已经是非 root（--user 启动，或镜像被改成 USER appuser），无从 chown。
        os.execvp(command[0], command)
        return 0

    target = data_dir()
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        print(f"[entrypoint] 无法创建数据目录 {target}: {exc}", file=sys.stderr, flush=True)

    if _truthy(os.environ.get("DEEPSEEKER_SKIP_CHOWN")):
        print("[entrypoint] DEEPSEEKER_SKIP_CHOWN 已设置，跳过属主修正", flush=True)
    else:
        changed, failed = fix_ownership(target)
        if changed:
            print(
                f"[entrypoint] 已修正 {changed} 个路径的属主为 {APP_UID}:{APP_GID}（{target}）",
                flush=True,
            )
        for line in failed[:5]:
            print(f"[entrypoint] 属主修正失败（忽略）{line}", file=sys.stderr, flush=True)

    # 顺序不能反：先 setgid 再 setuid，否则降权后就没有改 gid 的权限了。
    # 清空附加组，避免继承 root 的组权限。
    try:
        os.setgroups([])
    except OSError:
        pass
    os.setgid(APP_GID)
    os.setuid(APP_UID)

    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
