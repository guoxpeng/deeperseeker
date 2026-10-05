"""容器健康检查探针（替代原先依赖 curl 的写法）。

语义上区分「进程挂了」和「进程活着但令牌池为空」：

    200 -> 健康
    503 -> 存活但降级（没有可用令牌），容器仍视为 healthy，
           因为这是业务状态而不是进程故障；真正的进程故障会连接失败。
    其它 -> unhealthy

用标准库实现，这样镜像里不必再装 curl。
"""

import os
import sys
import urllib.error
import urllib.request

PORT = os.getenv("PORT", "4000")
URL = f"http://127.0.0.1:{PORT}/health"


def main():
    try:
        with urllib.request.urlopen(URL, timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except urllib.error.HTTPError as exc:
        # 503 是 /health 的「降级」语义：服务在跑，只是池里没有可用令牌。
        return 0 if exc.code == 503 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
