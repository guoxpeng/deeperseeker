#!/bin/sh
# =============================================================================
# 定制完整性校验：确认「我们自己的改动」都还在。
#
# 为什么需要它
# ------------
# 双分支流程保证「同步上游」这一步不会冲突，但理论上仍有一种坏情况：
# 上游改动了同一个文件，git 自动合并成功、却把我们的那部分改动覆盖掉了。
# 这种「静默丢失」不会报冲突，只能靠事后核对发现。本脚本就是那道核对。
#
# 用法（仓库根目录或任意目录都行）：
#   ./deploy/verify-custom.sh
#   MIRROR_BRANCH=main ./deploy/verify-custom.sh
#
# 退出码：
#   0  全部在位
#   1  有锚点缺失 —— 我们的改动确实丢了，需要人工介入
# =============================================================================
set -u

# 默认校验「脚本所在的这个仓库」。也允许用 VERIFY_REPO_ROOT 指定别的检出
# （测试、或想核对另一份 clone 时用）。
if [ -n "${VERIFY_REPO_ROOT:-}" ]; then
    cd "$VERIFY_REPO_ROOT"
else
    cd "$(dirname "$0")/.."
fi

MIRROR_BRANCH="${MIRROR_BRANCH:-main}"

FAIL=0
PASS_N=0

ok()   { PASS_N=$((PASS_N + 1)); }
fail() { FAIL=$((FAIL + 1)); printf '  \033[31m✗\033[0m %s\n' "$1"; }

echo "=== 定制完整性校验 ==="

# --- 1. 必须存在的文件 -------------------------------------------------------
echo
echo "[1/4] 我们新增的文件"
MUST_EXIST="
tls_helper.py
healthcheck.py
docker-entrypoint.py
update.sh
UPDATE.md
.gitattributes
README.zh-CN.md
requirements-playwright.txt
static/favicon.svg
deploy/nas-deploy.sh
deploy/run-tests.sh
tests/test_users.py
tests/test_public_https.py
tests/test_tls.py
tests/test_docker_entrypoint.py
tests/test_identity_profiles.py
"
for f in $MUST_EXIST; do
    if [ -f "$f" ]; then
        ok
    else
        fail "缺少文件 $f"
    fi
done
echo "  检查 $(printf '%s\n' $MUST_EXIST | grep -c .) 个文件"

# --- 2. 关键代码锚点 ---------------------------------------------------------
# 每条写成 "文件:必须出现的字面串"。用 grep -F 做固定串匹配，避免正则转义问题。
echo
echo "[2/4] 关键代码锚点"
check_symbol() {
    file="$1"; pattern="$2"; label="$3"
    if [ ! -f "$file" ]; then
        fail "$label —— 文件 $file 不存在"
        return
    fi
    if grep -qF -- "$pattern" "$file"; then
        ok
    else
        fail "$label —— $file 里找不到 '$pattern'"
    fi
}

check_symbol app.py '_normalize_public_https_url' '控制台：外部 HTTPS 入口地址规范化'
check_symbol app.py 'PUBLIC_HTTPS_URL'            '控制台：DEEPSEEKER_PUBLIC_HTTPS_URL 支持'
check_symbol app.py '_public_https_info'          '控制台：HTTPS 接入信息组装'
check_symbol app.py 'SESSION_USERS'               '控制台：会话到用户的映射'
check_symbol app.py 'get_current_admin'           '控制台：管理员鉴权'
check_symbol app.py '_prune_admin_sessions'       '控制台：管理员会话清理'
check_symbol functions.py 'def validate_username' '账号：用户名校验'
check_symbol functions.py 'def create_user'       '账号：创建用户'
check_symbol functions.py 'def delete_user'       '账号：删除用户'
check_symbol functions.py 'def set_user_password' '账号：重置密码'
check_symbol functions.py 'scrypt'                '账号：scrypt 口令哈希'
check_symbol templates/dashboard.html 'api.public_https' '界面：HTTPS 接入区块'
check_symbol templates/dashboard.html '账号管理'          '界面：账号管理区块'
check_symbol Dockerfile 'docker-entrypoint.py'    '容器：降权入口脚本'
check_symbol Dockerfile 'healthcheck.py'          '容器：标准库健康探针'
check_symbol docker-compose.yml '${DEEPSEEKER_BIND' '容器：端口绑定地址参数化'
check_symbol .gitattributes 'eol=lf'              '仓库：脚本换行符钉死为 LF'
check_symbol deploy/nas-deploy.sh 'checkout -f -B' '部署：切分支不挪动 main'
check_symbol deploy/nas-deploy.sh 'DEPLOY_DRY_RUN' '部署：预演模式'
check_symbol update.sh 'verify-custom.sh'          '同步：内置定制校验'
echo "  （以上未列出的即为通过）"

# --- 3. 脚本可执行位 ---------------------------------------------------------
echo
echo "[3/4] 脚本可执行位"
for f in update.sh deploy/nas-deploy.sh deploy/run-tests.sh; do
    if [ ! -f "$f" ]; then
        fail "$f 不存在"
    elif [ -x "$f" ]; then
        ok
    else
        fail "$f 丢了可执行位（git update-index --chmod=+x $f）"
    fi
done

# --- 4. main 必须是纯上游镜像 ------------------------------------------------
echo
echo "[4/4] 分支不变量"
if git rev-parse --verify --quiet "$MIRROR_BRANCH" >/dev/null; then
    N="$(git rev-list --count "$MIRROR_BRANCH" 2>/dev/null || echo 0)"
    if git rev-parse --verify --quiet upstream/main >/dev/null; then
        AHEAD="$(git rev-list --count upstream/main.."$MIRROR_BRANCH")"
        if [ "$AHEAD" -eq 0 ]; then
            ok
            echo "  $MIRROR_BRANCH 是纯上游镜像（相对 upstream/main 领先 0 个提交）"
        else
            fail "$MIRROR_BRANCH 上有 $AHEAD 个不属于上游的提交 —— 它不再是纯镜像了"
        fi
    else
        echo "  （没有 upstream remote，跳过镜像纯度检查）"
    fi
else
    fail "分支 $MIRROR_BRANCH 不存在"
fi

# --- 汇总 --------------------------------------------------------------------
echo
if [ "$FAIL" -eq 0 ]; then
    printf '\033[32m✅ 定制完整性校验通过（%d 项）\033[0m\n' "$PASS_N"
    exit 0
else
    printf '\033[31m❌ 定制完整性校验失败：%d 项缺失\033[0m\n' "$FAIL"
    echo
    echo "如果这是在 update.sh 同步之后发生的，很可能是上游改动覆盖了我们的代码。"
    echo "本次合并尚未推送，可以整体撤销："
    echo "    git reset --hard ORIG_HEAD"
    echo "然后看看到底哪个文件被改了："
    echo "    git log --oneline ORIG_HEAD..HEAD"
    exit 1
fi
