#!/bin/sh
# =============================================================================
# 容器内测试门禁：用「和生产同一个镜像」跑 tests/，避免「本机能过、容器里挂」。
#
# 为什么不能直接 `docker compose run ... pytest tests`：
#   .dockerignore 把 tests/ 与 requirements-dev.txt 排除在镜像之外（镜像要小），
#   所以必须运行时把 tests/ 挂进去，并在容器里临时装 pytest。
#
# 用法（在仓库根目录，或任意目录都行）：
#   ./deploy/run-tests.sh                        # 跑全部
#   ./deploy/run-tests.sh tests/test_users.py    # 只跑指定文件
#   SKIP_BUILD=1 ./deploy/run-tests.sh           # 复用已有镜像，不重建
#   IMAGE=deeperseeker:local ./deploy/run-tests.sh
# =============================================================================
set -eu

cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-deeperseeker:local}"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    echo ">>> 构建镜像 $IMAGE"
    docker build -t "$IMAGE" .
fi

if [ "$#" -eq 0 ]; then
    set -- tests
fi

echo ">>> 在容器里跑：$*"
# 内层 sh 用 sh -c '...' sh "$@" 传参，保证参数原样到达 pytest。
docker run --rm \
    -v "$PWD/tests:/app/tests:ro" \
    --entrypoint sh \
    "$IMAGE" \
    -c 'pip install -q pytest pytest-asyncio httpx && exec python -m pytest "$@" -q' sh "$@"
