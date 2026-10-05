# 维护与升级

这个仓库是 [`AmanCode22/deeperseeker`](https://github.com/AmanCode22/deeperseeker) 的
**带定制分支的 fork**。上游很活跃，所以这里用「双分支」把「跟上游」和「自己的改动」
彻底分开，让同步永远不会因为冲突而卡住。

## 分支约定

| 分支 | 作用 | 规则 |
| --- | --- | --- |
| `main` | **上游镜像** | 只允许快进到 `upstream/main`，**绝不在这里提交本地改动** |
| `custom` | **你的定制** | 日常开发、部署都用它；上游的更新通过合并 `main` 进来 |

好处：`main` 永远干净，`update.sh` 的「快进」这一步**不可能冲突**；
冲突只可能出现在最后「`main` 合并进 `custom`」这一步，范围清晰、好处理。

## 一键同步上游

```sh
./update.sh            # 拉上游 -> 快进 main -> 合并进 custom
./update.sh --check    # 只看上游有没有新提交，什么都不改
```

脚本会：

1. 检查工作区是否干净（不干净直接拒绝，避免把半成品搅进合并）；
2. 确保 `upstream` remote 指向 `AmanCode22/deeperseeker`；
3. `git fetch upstream --tags --prune`；
4. 切到 `main`，`git merge --ff-only upstream/main`；
5. 切到 `custom`，`git merge main`；有冲突就打印处理步骤并以非 0 退出。

如果第 4 步报「`main` 不是纯上游镜像」，说明有提交被误提到 `main` 上了，
按脚本打印的命令把提交挪到 `custom` 即可。

## 跑测试门禁

`tests/` 与 `requirements-dev.txt` 被 `.dockerignore` 排除在镜像之外（镜像要小），
所以不能直接 `docker compose run ... pytest tests`。用配套脚本：

```sh
./deploy/run-tests.sh                        # 构建镜像并跑全部测试
./deploy/run-tests.sh tests/test_users.py    # 只跑指定文件
SKIP_BUILD=1 ./deploy/run-tests.sh           # 复用已有镜像
```

它做的是：`docker build` → `docker run` 时把 `tests/` 只读挂进 `/app/tests` →
容器里临时装 pytest → 跑测试。用的是**和生产同一个镜像**，所以不会出现
「本机能过、容器里挂」。

## 部署到 NAS

NAS 上的 `/root/deeperseeker` 是这个仓库的 git clone。之后每次更新，
在 NAS 上跑一条命令即可：

```sh
/root/deeperseeker/deploy/nas-deploy.sh
```

它会：`git fetch origin` → 切到 `custom`（首次会自动从 `main` 切过去）→
`docker compose build` → `docker compose up -d` → 等 healthy →
验收（HTTP / HTTPS / 数据条数）→ 打印回滚命令。

预演（只切分支、不重建镜像、不重启容器）：

```sh
DEPLOY_DRY_RUN=1 /root/deeperseeker/deploy/nas-deploy.sh
```

### 部署机的差异一律写进 `.env`

`.env` 不在 git 里，`git reset --hard` / `git checkout -f` 都不会碰它。
**需要保留的部署机差异（例如绑定 `0.0.0.0`）都写进 `.env`，不要改
`docker-compose.yml`** —— 后者是入库的，会被重置。

NAS 上必须有一行：

```sh
DEEPSEEKER_BIND=0.0.0.0
```

少了它，compose 会把端口绑到默认的 `127.0.0.1`，宿主上的反向代理
（`https://192.168.5.3:14000`）就连不上容器了。`nas-deploy.sh` 会对此告警。

## 推送到自己的仓库

```sh
git push origin custom    # 你的定制（日常只需要推这个）
git push origin main      # 仅当 main 跟上游前进过（可选但推荐）
```

> 本机 `git push` 可能会被沙箱杀掉；如果失败，改用仓库配套的 GitHub API 推送脚本。

## 故障排查

**仓库不能是浅克隆（shallow）。** 浅克隆会让推送、合并、`merge-base` 出现各种
莫名其妙的问题。检查与修复：

```sh
git rev-parse --is-shallow-repository   # 必须输出 false
git fetch --unshallow origin            # 若是 true，用这个补齐历史
```

克隆时也不要加 `--depth`。

**`docker compose build` 后镜像名变了。** `docker-compose.yml` 里显式写了
`image: deeperseeker:local`；否则 compose 会自动生成
`deeperseeker-deeperseeker` 这类名字，写 `docker run` 时不好引用。

## 为什么不用 `docker-compose.override.yml`

也可以用 override 文件覆盖端口，但 `ports` 在 compose 里是**追加**语义，
两份 `ports` 会同时生效并抢同一个宿主端口。所以这里改用变量插值
（`${DEEPSEEKER_BIND:-127.0.0.1}`），把差异收进 `.env`。
