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
./update.sh            # 拉上游 -> 快进 main -> 合并进 custom -> 校验定制完好
./update.sh --check    # 只看上游有没有新提交，什么都不改
```

脚本会：

1. 检查工作区是否干净（不干净直接拒绝，避免把半成品搅进合并）；
2. 确保 `upstream` remote 指向 `AmanCode22/deeperseeker`；
3. `git fetch upstream --tags --prune`；
4. 把还原点分支 `presync` 指到当前的 `custom`，并做一次基线校验
   （确认定制**本来**是完好的，这样后面万一报错能正确归因）；
5. 切到 `main`，`git merge --ff-only upstream/main`；
6. 切到 `custom`，`git merge main`；有冲突就打印处理步骤并以非 0 退出；
7. **校验我们的定制有没有被吞掉**（见下一节），不过就拒绝继续、提示先别推送。

如果第 5 步报「`main` 不是纯上游镜像」，说明有提交被误提到 `main` 上了，
按脚本打印的命令把提交挪到 `custom` 即可。

## 为什么同步不会弄丢我们的改动

三道保障。

**第一道：结构上让冲突不可能发生在上游同步那一步。**
`main` 只做快进、永不提交本地改动，所以第 5 步永远不可能冲突。
冲突只可能出现在第 6 步「`main` 合并进 `custom`」，而且一旦冲突脚本就停下、
不自动做任何决定，还给出 `git merge --abort` 的回退方式。

**第二道：结构保证不了的地方，用核对兜住。**
即使第 6 步「自动合并成功」，理论上仍有一种坏情况：上游改动了同一个文件，
git 合并成功、却把我们的那部分改动覆盖掉了 —— 这种静默丢失不报冲突。
所以第 7 步会跑 `deploy/verify-custom.sh`：

```sh
./deploy/verify-custom.sh      # 随时可以手动跑
```

它把「我们的定制」固化成一份可执行清单（41 项），逐项核对：

1. 我们新增的文件必须都在（`tls_helper.py`、`docker-entrypoint.py`、各测试 …）；
2. 关键代码锚点必须都在（`app.py` 的 `SESSION_USERS`、`functions.py` 的
   `create_user`、模板里的 HTTPS 接入区块、compose 里的端口插值变量 …）；
3. 四个脚本在 **git 索引里**的 mode 必须是 `100755`
   （看索引而不是本地权限位 —— 那才是决定 NAS 检出后能否执行的东西）；
4. `main` 必须仍是纯上游镜像。

另外还会做一次**文件级核对**：同步前 `custom` 相对 `main` 有差异的每个文件，
同步后必须**仍然有差异**。如果某个文件变得和 `main` 一模一样，说明我们那份改动
不见了 —— 脚本会把它列出来。

**第三道：随时能回到同步之前。**
每次同步前，脚本会把本地分支 `presync` 指到当时的 `custom`：

```sh
git reset --hard presync     # 回到最近一次同步之前
```

`presync` 只存在于本地，不会被 push。

任何一项不过，`update.sh` 都会以非 0 退出并**明确说「先别推送」**，同时给出：

```sh
git diff main..custom -- <列出的文件>    # 看看到底怎么了
git reset --hard ORIG_HEAD              # 撤销最近一次合并
git reset --hard presync                # 回到「同步之前」
```

> 解决冲突时**不要**把整个文件切成上游版本（`git checkout --theirs <文件>`），
> 那会把我们的改动一起丢掉。解决完冲突、commit 之后，务必再跑一次
> `./deploy/verify-custom.sh`。

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

**`git fetch origin` 拉不到 `custom` 分支。** 如果仓库当初是用 `--single-branch`
（或 `--depth`）克隆的，`remote.origin.fetch` 只会配 `main` 一条，远端其它分支
永远拉不下来：

```sh
git config --get-all remote.origin.fetch
# 若只输出 +refs/heads/main:refs/remotes/origin/main，改成全分支：
git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
git fetch origin --prune
```

**`docker compose build` 后镜像名变了。** `docker-compose.yml` 里显式写了
`image: deeperseeker:local`；否则 compose 会自动生成
`deeperseeker-deeperseeker` 这类名字，写 `docker run` 时不好引用。

## 为什么不用 `docker-compose.override.yml`

也可以用 override 文件覆盖端口，但 `ports` 在 compose 里是**追加**语义，
两份 `ports` 会同时生效并抢同一个宿主端口。所以这里改用变量插值
（`${DEEPSEEKER_BIND:-127.0.0.1}`），把差异收进 `.env`。
