FROM python:3.12-slim

# 无缓冲输出让日志实时可见；不写 .pyc 让容器层更干净。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 依赖层单独缓存：只改代码时不会重装依赖。
# 注意 requirements.txt 已不含 playwright / Chromium（默认路径不再需要）。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /app/data 是唯一需要持久化的目录（SQLite、自动生成的 API Key、Cookie 文件），
# 用软链接把运行时默认路径桥接过去，再整体交给非 root 用户。
RUN mkdir -p /app/data \
    && ln -sf /app/data/deeperseeker.db /app/deeperseeker.db \
    && ln -sf /app/data/aws_cookies_deepseek.json /app/aws_cookies_deepseek.json \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

ENV DB_PATH=/app/data/deeperseeker.db \
    DEEPSEEKER_COOKIE_PATH=/app/data/aws_cookies_deepseek.json \
    HOST=0.0.0.0

EXPOSE 4000
# HTTPS 监听端口（需要 DEEPSEEKER_HTTPS_ENABLED=1 才会真正监听）
EXPOSE 4443

# 用标准库探针代替 curl，省掉 apt 层（镜像更小、构建更快）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]

# 容器以 root 进入入口脚本，由它把 /app/data 的属主修正为 uid 10001 后再
# setuid 降权执行 CMD。这样从旧版本升级时不需要手动 chown 数据卷。
# 若用 `docker run --user 10001` 直接以非 root 启动，入口脚本会跳过 chown。
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
CMD ["python", "app.py"]

# 若要回退到备用 Playwright Cookie 机制：
#   1) COPY requirements-playwright.txt ./ && pip install -r requirements-playwright.txt && playwright install --with-deps chromium
#   2) 以 root 装好 xvfb 后改用：
#      CMD ["sh", "-c", "xvfb-run -a -s '-screen 0 1280x720x24' python app.py"]
