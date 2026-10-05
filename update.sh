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

# --- 3. 快进 main（上游镜像必须是纯的）--------------------------------------
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

# --- 4. 合并进定制分支 -------------------------------------------------------
info "切到 $WORK_BRANCH 并合并 $MIRROR_BRANCH…"
git checkout "$WORK_BRANCH"
if git merge "$MIRROR_BRANCH" --no-edit; then
    ok "已合并到 $WORK_BRANCH（当前 $(git rev-parse --short HEAD)）。"
    echo
    echo "接下来："
    echo "  1) 跑测试确认没被上游改动影响：  docker compose build && docker compose run --rm --entrypoint sh deeperseeker -c 'python -m pytest tests -q'"
    echo "  2) 部署到 NAS：                  ./deploy/nas-deploy.sh（在 NAS 上执行）"
    echo "  3) 推到你自己的仓库：            git push origin $WORK_BRANCH"
else
    echo
    warn "合并有冲突，需要手工解决："
    echo "  git status                    # 看哪些文件冲突"
    echo "  # 改完冲突文件后："
    echo "  git add <文件> && git commit"
    echo "  # 想放弃这次合并："
    echo "  git merge --abort"
    exit 1
fi
