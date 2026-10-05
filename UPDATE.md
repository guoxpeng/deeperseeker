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

合并完先跑一遍测试再部署：

```sh
docker compose build
docker compose run --rm --entrypoint sh deeperseeker -c 'python -m pytest tests -q'
```

## 部署到 NAS

NAS 上的 `/root/deeperseeker` 是这个仓库的 git clone。一次性的初始设置：

```sh
cd /root/deeperseeker
git remote set-url origin https://github.com/guoxpeng/deeperseeker.git
git fetch origin
git checkout custom
```

之后每次更新，在 NAS 上跑一条命令即可：

```sh
/root/deeperseeker/deploy/nas-deploy.sh
```

它会：`git fetch` → `git reset --hard origin/custom` → `docker compose build` →
`docker compose up -d` → 等 healthy → 验收（HTTP / HTTPS / 数据条数）→ 打印回滚命令。

`.env` 与数据卷（`deeperseeker_data`）都在 `.gitignore` 里，`git reset --hard`
不会碰它们。**部署机上需要保留的差异（例如绑定 `0.0.0.0`）一律写进 `.env`**，
不要改 `docker-compose.yml` —— 那个文件是入库的，会被重置。

## 推送到自己的仓库

```sh
git push origin main      # 让 fork 的 main 与上游保持一致（可选但推荐）
git push origin custom    # 你的定制
```

> 本机 `git push` 可能会被沙箱杀掉；如果失败，改用仓库配套的 GitHub API 推送脚本。

## 为什么不用 `docker-compose.override.yml`

也可以用 override 文件覆盖端口，但 `ports` 在 compose 里是**追加**语义，
两份 `ports` 会同时生效并抢同一个宿主端口。所以这里改用变量插值
（`${DEEPSEEKER_BIND:-127.0.0.1}`），把差异收进 `.env`。
