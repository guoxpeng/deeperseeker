#!/bin/sh
# =============================================================================
# NAS 一键部署：拉取你自己仓库的最新代码 -> 重建镜像 -> 重启容器 -> 验收
#
# 在 NAS 上执行（需要 root 或 docker 权限）：
#   /root/deeperseeker/deploy/nas-deploy.sh
#
# 前置条件（只需做一次）：
#   cd /root/deeperseeker
#   git remote set-url origin https://github.com/guoxpeng/deeperseeker.git
#   git checkout custom
#
# 设计要点：
#   * 脚本会先把「自己」复制到 /tmp 再执行 —— 因为脚本本身在仓库里，
#     `git reset --hard` 会把它替换掉，而正在执行的 shell 脚本被就地替换
#     会让解释器读错行。
#   * .env 与数据卷都不在 git 里（.gitignore 已忽略），git reset 不会碰它们。
#   * 验收不过不会自动回滚，但会打印回滚命令。
#
# 环境变量：
#   DEPLOY_BRANCH  默认 custom
#   DEPLOY_REMOTE  默认 origin
#   DEPLOY_DIR     默认 /root/deeperseeker
# =============================================================================
set -eu

BRANCH="${DEPLOY_BRANCH:-custom}"
REMOTE="${DEPLOY_REMOTE:-origin}"
REPO_DIR="${DEPLOY_DIR:-/root/deeperseeker}"
HEALTH_URL="http://127.0.0.1:4000/health"
HTTPS_URL="${DEPLOY_HTTPS_URL:-https://192.168.5.3:14000/}"

# --- 自举：复制到 /tmp 再跑，避免 git reset 把自己换掉 ----------------------
if [ "${DEPLOY_REEXEC:-}" != "1" ]; then
    TMP="/tmp/deeperseeker-deploy.$$.sh"
    cp "$0" "$TMP"
    chmod +x "$TMP"
    DEPLOY_REEXEC=1 \
    DEPLOY_BRANCH="$BRANCH" \
    DEPLOY_REMOTE="$REMOTE" \
    DEPLOY_DIR="$REPO_DIR" \
    DEPLOY_HTTPS_URL="$HTTPS_URL" \
    exec sh "$TMP" "$@"
fi

cd "$REPO_DIR"

echo "=============== 0. 基线 ==============="
OLD_COMMIT="$(git rev-parse HEAD)"
echo "  当前提交: $(git rev-parse --short HEAD)  ($(git log -1 --format=%s))"
echo "  数据卷:   $(docker volume ls --format '{{.Name}}' | grep deeperseeker || echo '?')"
echo "  .env:     $(md5sum .env 2>/dev/null | cut -d' ' -f1 || echo '缺失')"

echo
echo "=============== 1. 拉取 $REMOTE/$BRANCH ==============="
git fetch "$REMOTE" --prune
git reset --hard "$REMOTE/$BRANCH"
git --no-pager log --oneline -5

echo
echo "=============== 2. 校验 .env 未被改动 ==============="
echo "  .env md5: $(md5sum .env 2>/dev/null | cut -d' ' -f1 || echo '缺失')"
if [ ! -f .env ]; then
    echo "  ⚠️  没有 .env —— 端口会退回默认的 127.0.0.1 绑定，局域网将无法访问。"
    echo "     请在 .env 里设置 DEEPSEEKER_BIND=0.0.0.0"
fi

echo
echo "=============== 3. 重建镜像 ==============="
docker compose build

echo
echo "=============== 4. 重启容器 ==============="
docker compose up -d

echo
echo "=============== 5. 等 healthy ==============="
i=0
HEALTH=unknown
while [ "$i" -lt 40 ]; do
    HEALTH="$(docker inspect deeperseeker --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)"
    echo "  [$((i * 3))s] health=$HEALTH"
    [ "$HEALTH" = "healthy" ] && break
    i=$((i + 1))
    sleep 3
done

echo
echo "=============== 6. 验收 ==============="
printf '  http  4000/health   -> '
curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 "$HEALTH_URL" || echo FAIL
printf '  https 14000/        -> '
curl -sSk -o /dev/null -w '%{http_code}\n' --max-time 8 "$HTTPS_URL" || echo FAIL
echo "  容器:"
docker ps --filter name=deeperseeker --format '    {{.Names}}  {{.Status}}  {{.Ports}}'
echo "  数据:"
docker exec deeperseeker python -c "
import sqlite3
d = sqlite3.connect('/app/data/deeperseeker.db')
print('    tokens =', d.execute('select count(*) from tokens').fetchone()[0])
print('    sessions =', d.execute('select count(*) from sessions').fetchone()[0])
d.close()
" 2>/dev/null || echo "    (读库失败)"

echo
echo "=============== 7. 回滚方法 ==============="
echo "  cd $REPO_DIR && git reset --hard $OLD_COMMIT && docker compose up -d --build"
echo
if [ "$HEALTH" = "healthy" ]; then
    echo "✅ 部署完成，health=$HEALTH"
else
    echo "❌ 容器未达到 healthy（当前 $HEALTH），请查看：docker logs --tail 50 deeperseeker"
    exit 1
fi
