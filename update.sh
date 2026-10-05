#!/usr/bin/env sh
# =============================================================================
# 一键同步上游：把 AmanCode22/deeperseeker 的新提交合并进来，同时保住本地定制。
#
# 分支约定（见 UPDATE.md）：
#   main    —— 上游镜像。永远只做快进，绝不在这里提交本地改动。
#   custom  —— 你的定制分支。日常开发、部署都用这个。
#
# 本脚本做的事：
#   git fetch upstream  ->  main 快进到 upstream/main  ->  custom 合并 main
#   ->  校验我们的定制有没有被吞掉
#
# 用法：
#   ./update.sh            同步并合并到 custom
#   ./update.sh --check    只看上游有没有新东西，不改动任何分支
#
# 环境变量：
#   UPSTREAM_URL    默认 https://github.com/AmanCode22/deeperseeker.git
#   WORK_BRANCH     默认 custom
#   MIRROR_BRANCH   默认 main
# =============================================================================
set -eu

UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/AmanCode22/deeperseeker.git}"
UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
MIRROR_BRANCH="${MIRROR_BRANCH:-main}"
WORK_BRANCH="${WORK_BRANCH:-custom}"
BACKUP_BRANCH="${BACKUP_BRANCH:-presync}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

info() { printf '\033[36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# 必须从仓库里跑（也允许从子目录跑）
git rev-parse --show-toplevel >/dev/null 2>&1 || die "当前目录不是 git 仓库。"
cd "$(git rev-parse --show-toplevel)"

# --- 0. 工作区必须干净：否则 checkout 会失败或把改动搅进去 --------------------
if [ -n "$(git status --porcelain)" ]; then
    warn "工作区有未提交的改动："
    git status --short
    die "先 git add/commit 或 git stash，再跑本脚本。"
fi

# --- 1. 确保 upstream remote 存在 --------------------------------------------
if git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
    CURRENT_URL="$(git remote get-url "$UPSTREAM_REMOTE")"
    if [ "$CURRENT_URL" != "$UPSTREAM_URL" ]; then
        warn "$UPSTREAM_REMOTE 现在指向 $CURRENT_URL，改为 $UPSTREAM_URL"
        git remote set-url "$UPSTREAM_REMOTE" "$UPSTREAM_URL"
    fi
else
    info "添加 $UPSTREAM_REMOTE -> $UPSTREAM_URL"
    git remote add "$UPSTREAM_REMOTE" "$UPSTREAM_URL"
fi

# --- 2. 拉取 ----------------------------------------------------------------
info "拉取上游（$UPSTREAM_REMOTE）…"
git fetch "$UPSTREAM_REMOTE" --tags --prune

BEHIND="$(git rev-list --count "$MIRROR_BRANCH..$UPSTREAM_REMOTE/main")"
if [ "$BEHIND" -eq 0 ]; then
    ok "上游没有新提交，$MIRROR_BRANCH 已经是最新。"
else
    info "上游有 $BEHIND 个新提交："
    git --no-pager log --oneline --no-decorate "$MIRROR_BRANCH..$UPSTREAM_REMOTE/main" | head -30
fi
[ "$CHECK_ONLY" -eq 1 ] && exit 0

# --- 3. 记录同步前的定制清单（合并后用来核对没被吞掉）------------------------
SNAPSHOT="$(mktemp 2>/dev/null || echo "/tmp/deeperseeker-custom-$$.txt")"
git diff --name-only "$MIRROR_BRANCH" "$WORK_BRANCH" > "$SNAPSHOT" 2>/dev/null || true
CUSTOM_TOTAL="$(grep -c . "$SNAPSHOT" 2>/dev/null || true)"
info "相对 $MIRROR_BRANCH 的定制文件共 ${CUSTOM_TOTAL:-0} 个，已记录，合并后会逐个核对。"

# 还原点：把 $BACKUP_BRANCH 指到同步前的 $WORK_BRANCH。
# 它只是个本地分支，不会被 push；好处是过很久也能一键回到「同步之前」，
# 而 ORIG_HEAD 只记得最近一次操作。
git branch -f "$BACKUP_BRANCH" "$WORK_BRANCH" 2>/dev/null || true
info "还原点：$BACKUP_BRANCH -> 同步前的 $WORK_BRANCH（$(git rev-parse --short "$WORK_BRANCH")）"

# 基线：先确认定制本来是完好的，这样万一第 6 步报错能正确归因。
if [ -f deploy/verify-custom.sh ]; then
    if MIRROR_BRANCH="$MIRROR_BRANCH" sh ./deploy/verify-custom.sh >/dev/null 2>&1; then
        info "基线校验：当前定制完好。"
    else
        warn "基线校验：当前定制**本来就不完整**（与本次同步无关）。"
        warn "建议先单独跑 ./deploy/verify-custom.sh 看看，再决定要不要继续同步。"
    fi
fi

# --- 4. 快进 main（上游镜像必须是纯的）--------------------------------------
info "切到 $MIRROR_BRANCH 并快进…"
git checkout "$MIRROR_BRANCH"
if ! git merge --ff-only "$UPSTREAM_REMOTE/main" >/dev/null 2>&1; then
    die "$MIRROR_BRANCH 不是纯上游镜像（上面有本地提交）。
把那些提交挪到 $WORK_BRANCH 上：
    git branch -f $WORK_BRANCH $MIRROR_BRANCH
    git reset --hard $UPSTREAM_REMOTE/main
    git checkout $WORK_BRANCH
然后再跑一次本脚本。"
fi
ok "$MIRROR_BRANCH -> $(git rev-parse --short HEAD)"

# --- 5. 合并进定制分支 -------------------------------------------------------
info "切到 $WORK_BRANCH 并合并 $MIRROR_BRANCH…"
git checkout "$WORK_BRANCH"
if git merge "$MIRROR_BRANCH" --no-edit; then
    ok "已合并到 $WORK_BRANCH（当前 $(git rev-parse --short HEAD)）。"
else
    echo
    warn "合并有冲突，需要手工解决："
    echo "  git status                    # 看哪些文件冲突"
    echo "  # 改完冲突文件后："
    echo "  git add <文件> && git commit"
    echo "  # 想放弃这次合并："
    echo "  git merge --abort"
    echo
    warn "⚠️  解决冲突时不要把整个文件切成上游版本 —— 那会把我们的改动一起丢掉。"
    echo "  解决完、commit 之后，务必再核对一遍定制还在不在："
    echo "      ./deploy/verify-custom.sh"
    echo "      git diff $MIRROR_BRANCH..$WORK_BRANCH   # 看我们的改动是否还在"
    exit 1
fi

# --- 6. 校验定制没被吞掉 -----------------------------------------------------
# 双分支流程保证第 4 步不可能冲突，但「git 自动合并成功、却把我们的改动覆盖掉」
# 这种静默丢失仍有可能，只能靠事后核对发现。
echo
echo "=============== 6. 校验定制未被吞掉 ==============="
VERIFY_RC=0
if [ -f deploy/verify-custom.sh ]; then
    MIRROR_BRANCH="$MIRROR_BRANCH" sh ./deploy/verify-custom.sh || VERIFY_RC=$?
else
    warn "没有 deploy/verify-custom.sh，跳过锚点校验。"
fi

# 文件级核对：同步前我们改过的文件，现在是否仍与 main 不同。
# 如果某个文件现在和 main 一模一样，说明我们的那份改动不见了。
LOST_LIST=""
while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ ! -e "$f" ]; then
        LOST_LIST="${LOST_LIST}  ${f}   （文件不存在了）
"
    elif git diff --quiet "$MIRROR_BRANCH" -- "$f" 2>/dev/null; then
        LOST_LIST="${LOST_LIST}  ${f}
"
    fi
done < "$SNAPSHOT"
rm -f "$SNAPSHOT" 2>/dev/null || true

if [ -n "$LOST_LIST" ]; then
    echo
    warn "以下文件同步后与 $MIRROR_BRANCH 完全一致（同步前它们是有差异的）："
    printf '%s' "$LOST_LIST"
    warn "多数情况是上游采纳了同样的改动；但也可能是我们的改动被覆盖了。"
    warn "本次合并还没推送，可以先看一眼再决定："
    echo "    git diff $MIRROR_BRANCH..$WORK_BRANCH -- <上面的文件>"
    echo "    git reset --hard ORIG_HEAD      # 撤销这次合并"
    echo "    git reset --hard $BACKUP_BRANCH # 回到「同步之前」的完整状态"
fi

# --- 汇总 --------------------------------------------------------------------
echo
if [ "$VERIFY_RC" -eq 0 ] && [ -z "$LOST_LIST" ]; then
    ok "定制完好，可以继续。"
    echo
    echo "接下来："
    echo "  1) 跑测试确认没被上游改动影响：  ./deploy/run-tests.sh"
    echo "  2) 推到你自己的仓库：            git push origin $WORK_BRANCH"
    echo "  3) 部署到 NAS：                  在 NAS 上跑 /root/deeperseeker/deploy/nas-deploy.sh"
else
    warn "校验发现问题，**先别推送**。上面已给出核对与撤销的命令。"
    exit 1
fi
