# DeeperSeeker

[English](README.md) | **简体中文**

用你自己的 DeepSeek 网页版账号，通过 **OpenAI / Anthropic 兼容接口**对外提供服务。
基于 FastAPI 的反向代理，支持工具调用、视觉理解与流式输出。

> 想在 Claude 桌面版里使用？见 [Claude Desktop 配置指南](CLAUDE_DESKTOP_SETUP.md)。

求解 DeepSeek PoW 挑战所用的 wasm 文件来自作者的另一个仓库：
<https://github.com/AmanCode22/deepseek_pow_solver/>。觉得有用的话，两个仓库都欢迎点星。

---

## ⚠️ 重要提醒

- **自动化调用违反 DeepSeek 的服务条款。**
- 请使用**专门注册的一次性小号**，**不要使用日常主账号**。
- 账号随时可能被封禁，**风险自负**。
- 请不要滥用服务端，尊重 DeepSeek 的限流，仅用于个人用途。

---

## 关于封号

先把结论说清楚：**没有任何配置能让自动化调用变得「安全」或「合规」。** 下面这些
只能降低风险，不能消除风险。

### 为什么绕过了 WAF 还是会封号

这两件事根本不在一个层面上：

- **绕过 AWS WAF** 解决的是「请求能不能发出去」。本项目靠伪装 Android 客户端
  请求头做到这一点 —— DeepSeek 不对携带 Android 客户端头的请求强制校验 WAF。
- **封号**是风控系统**事后**根据行为模式判定的。请求能稳定发出去，恰恰说明
  它的量和规律性足以被统计出来。

服务端代理天然会暴露这些特征，且无法在客户端代码里掩盖：

- 所有账号从**同一个出口 IP** 发出请求；
- **同一套 TLS 指纹**（aiohttp 的默认 ClientHello）；
- **机器化的时间分布**（人不会以恒定间隔提问）；
- 一个**新注册、零历史**的账号，从第一天起就高强度产出。

### 本项目做了什么（B11）

原版所有令牌共用同一套请求头，在风控眼里多个账号就是同一台设备。现在每个令牌
会按自身内容**稳定派生**一个身份（见 `.env.example` 的「客户端身份轮换」）：

| 模式 | 轮换字段 | 说明 |
|---|---|---|
| `off` | 无 | 完全保持原版行为 |
| `device`（默认） | `user-agent`、时区偏移 | 版本与语言保持固定 |
| `full` | 再叠加客户端版本、语言 | 更分散，但需自行维护版本池 |

几点必须说明：

- **稳定性是刻意设计的。** 同一个令牌永远对应同一台设备，重启不变、也不随请求
  跳动 —— 一个每次请求都换身份的设备，比一个从不换身份的设备**更**可疑。
- **`device` 是默认值，因为它是安全的那个。** 上游会校验客户端**版本号**，
  而 `user-agent` 只是描述性字段：真实 Android 设备的 UA 本来就因机型而异，
  任何 WAF 规则都不可能只放行某一个确切字符串。所以默认不动版本号。
- **轮换只降低「多账号被聚成一批」的相关性。** 12 个机型 × 6 个时区 = 72 种组合，
  5 个账号基本两两不同；但 8 个以上仍可能撞车（生日问题）。控制台会显示每个
  令牌的身份，撞车时改 `DEEPSEEKER_IDENTITY_SEED` 重新分配即可。
- **它挡不住上面那四条。** 出口 IP、TLS 指纹、时间分布都不在请求头里。

### 真正有效的做法是行为层面的

1. **绝不用主号。** 一旦封禁，聊天记录、历史、绑定的支付方式一起没。这条是
   唯一真正有效的措施。
2. **降低强度。** 单令牌并发默认已从 8 降到 2；重试次数保持默认 3，调高等于在
   同一个账号上反复敲门。
3. **不要把服务开成公开 API。** 一旦给别人用，请求量级失控，封号从「可能」
   变成「必然」，而且是一批号一起封。
4. **要长期稳定用，走 DeepSeek 官方 API。** 这是唯一不违反 ToS、也不会掉号的
   路径。

这个项目适合的定位是：**自己折腾、跑点实验，并随时准备好号没了。**

---

## 快速开始

### 方式一：本地 Python

```bash
python3 -m venv deeperseeker_env
source deeperseeker_env/bin/activate      # Windows: deeperseeker_env\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                      # 按需修改
python3 app.py
```

> **说明**：自 PR [#16](https://github.com/AmanCode22/deeperseeker/pull/16)
> （作者 [@alan7383](https://github.com/alan7383)）起，Playwright / Chromium 已降级为
> **备用方案**——DeepSeek 不会对携带 Android 客户端请求头的请求强制校验 AWS WAF。
> 因此默认安装**不再需要** `playwright install chromium` 和 `xvfb-run`，
> 内存占用更低、运行也更稳定。只有回退到备用 Cookie 抓取机制时才需要：
>
> ```bash
> pip install -r requirements-playwright.txt
> playwright install chromium
> ```

### 方式二：Docker / Podman（推荐用 Podman，支持无 root 运行）

```bash
cp .env.example .env

# Podman（推荐，无 root）
podman build -t deeperseeker .
podman run -d --name deeperseeker -p 4000:4000 --env-file .env deeperseeker

# Docker
docker build -t deeperseeker .
docker run -d --name deeperseeker -p 4000:4000 --env-file .env deeperseeker

# 或者用 Compose
podman-compose up -d
# docker compose up -d
```

> `docker-compose.yml` 只把端口绑定到 `127.0.0.1:4000`（仅本机可访问）。
> 需要对外暴露时自行修改端口映射。

**控制台地址**：<http://localhost:4000/>

> **从旧版本升级请注意**：镜像现在以非 root 用户（uid 10001）运行应用。
> 已经存在的命名数据卷仍归 root 所有，但**不需要你手动处理** ——
> `docker-entrypoint.py` 会在容器启动时自动把数据卷属主改成 uid 10001，
> 然后降权运行应用。直接 `docker compose up -d` 即可。
>
> 如果数据卷是只读挂载（此时改属主必然失败），设 `DEEPSEEKER_SKIP_CHOWN=1`
> 跳过这一步。全新安装不受影响。

---

## 配置（`.env`）

复制 `.env.example` 为 `.env`，按需修改。所有变量都是可选的。

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DEEPSEEKER_API_KEY` | 访问 API 所需的密钥 | 留空时自动生成强密钥并持久化 |
| `DEEPSEEKER_ADMIN_USER` | 控制台登录用户名 | `admin` |
| `DEEPSEEKER_ADMIN_PASSWORD` | 控制台登录密码 | `admin` |
| `HOST` | 裸机运行的绑定地址（`127.0.0.1` 仅本机，`0.0.0.0` 对外） | `127.0.0.1` |
| `PORT` | 服务端口 | `4000` |
| `DEEPSEEKER_MAX_HISTORY_TOKENS` | 首轮构建提示词时注入历史记录的 token 上限 | `24000` |
| `DEEPSEEKER_MAX_TOOL_RESULT_TOKENS` | 注入工具结果的 token 上限 | `12000` |
| `DEEPSEEKER_MEMORY_LIMIT_TOKENS` | 触发上下文滚动的已记忆上下文上限 | `393228` |
| `DEEPSEEKER_FIRST_MESSAGE_TOKENS` | 单条首轮消息的实测接受上限（仅供参考） | `974848` |
| `DEEPSEEKER_MAX_OUTPUT_TOKENS` | 实测单次输出上限（由 `/v1/models` 上报） | `8192` |
| `DEEPSEEKER_ROLLOVER_SAFETY_TOKENS` | 触发总结前预留的安全余量 | `24000` |
| `DEEPSEEKER_MAX_SUMMARY_TOKENS` | 交接摘要的生成预算 | `4096` |
| `DEEPSEEKER_PER_TOOL_RESULT_TOKENS` | 单条工具结果的输出上限 | `2000` |
| `DEEPSEEKER_HTTPS_ENABLED` | 是否额外监听 HTTPS | `0`（关闭） |
| `DEEPSEEKER_HTTPS_PORT` | HTTPS 监听端口 | `4443` |
| `DEEPSEEKER_HTTPS_ONLY` | 只跑 HTTPS，不再监听明文 HTTP | `0` |
| `DEEPSEEKER_HTTPS_CERT` / `DEEPSEEKER_HTTPS_KEY` | 自备证书与私钥路径（留空则自动生成自签证书） | 留空 |
| `DEEPSEEKER_HTTPS_SAN` | 自签证书额外覆盖的地址（逗号分隔，DNS 与 IP 混写） | 留空 |

其余稳定性、护栏、令牌池调度相关的开关见 `.env.example`（已附中文注释）。

---

## HTTPS 访问

默认仍然只监听明文 HTTP（与历史行为一致）。需要在**非 localhost** 的场景下使用
（例如 Claude Desktop、局域网其它设备），就必须开 HTTPS —— 那些客户端会直接
拒绝明文端点。

### 最快路径：自动自签证书

```bash
# .env
HOST=0.0.0.0                     # 只监听 127.0.0.1 的话，外部设备根本连不上
DEEPSEEKER_HTTPS_ENABLED=1
DEEPSEEKER_HTTPS_PORT=4443
DEEPSEEKER_HTTPS_SAN=nas.local   # 用固定主机名访问时写进来，可省略
```

启动后会自动生成一张自签证书（有效期 10 年），落在数据目录的
`tls/self-signed.crt`。证书的 SAN 自动包含：

- `localhost`、本机主机名与 `<主机名>.local`
- `127.0.0.1`、`::1`，以及默认出口网卡的局域网 IP
- `DEEPSEEKER_HTTPS_SAN` 里追加的地址

HTTPS 端口起来之后：

```bash
curl -k https://localhost:4443/v1/models -H "Authorization: Bearer <你的 API Key>"
```

> 浏览器会提示证书不受信任。把 `tls/self-signed.crt` 导入系统或浏览器的
> 「受信任的根证书颁发机构」，警告即消失（Chrome / Edge 都适用）。
> 控制台页面的「HTTPS 接入」一栏会直接给出证书路径，方便复制。

### 用自己签发的证书

配置 `DEEPSEEKER_HTTPS_CERT` 与 `DEEPSEEKER_HTTPS_KEY` 指向证书与私钥即可，
内网 CA 或正式 CA 签发的都行。两者必须成对提供 —— 只给一个会在启动时明确报错，
不会静默回退。

程序会在启动时校验证书与私钥是否配对、是否在有效期内，不通过就直接失败并给出
原因，而不是让 uvicorn 抛一句难懂的 SSL 异常。私钥带口令会报错（无人值守启动
无法交互输入口令）。

> ⚠️ **证书的 SAN 必须包含实际访问用的地址**。用 IP 访问就写 IP，用主机名访问
> 就写主机名 —— 这是最常见的「证书明明装好了浏览器还报错」的原因。

### 同时监听 HTTP 与 HTTPS

打开 HTTPS 后默认**两个都在跑**：HTTP 便于本机与 localhost 客户端，HTTPS 供
外部设备使用。想只保留 HTTPS：

```bash
DEEPSEEKER_HTTPS_ONLY=1
```

---

## 配置授权令牌

1. 打开**无痕 / 隐私窗口** → 访问 `chat.deepseek.com` → 登录
2. 按 F12 打开控制台（Console），执行：

   ```js
   JSON.parse(localStorage.getItem("userToken")).value
   ```

3. 把输出的原始令牌字符串粘贴到控制台页面（`/dashboard`），
   **去掉首尾引号**，然后**直接关闭无痕窗口**（不要点「退出登录」）。

---

## API 端点

**OpenAI 兼容**：Base URL `http://localhost:4000/v1`

- `POST /v1/chat/completions`（支持流式与非流式）
- `POST /v1/responses`（Responses API）
- `GET /v1/models`
- `POST /v1/files`
- `GET /v1/files/{file_id}`
- `GET /v1/files/{file_id}/content`

**Anthropic 兼容**：Base URL `http://localhost:4000`

- `POST /v1/messages`（也支持 `/messages`）
- `POST /v1/files/upload`

**鉴权**：使用 `.env` 中的 `DEEPSEEKER_API_KEY`，`Authorization: Bearer` 与 `x-api-key` 都支持。

**模型**：`v4.1flash` —— 当前唯一的默认 DeepSeek 模型（原生支持视觉，不再区分独立视觉档位）。
无论客户端传什么 `model` 值，所有请求都由它承接；历史别名（`instant`、`expert`、`vision`、
`anthropic/claude-*`）会被接受并归一化到 `v4.1flash`。同时以 `anthropic/claude-v4.1flash`
的形式暴露，供 Claude 桌面版自动发现。不传 `model` 时默认走 `v4.1flash`。

---

## 功能特性

- **多令牌池**：按「最少在途请求 + 最久未使用」调度，带软并发上限，避免请求同时压在一个账号上。
- **基于上下文的会话选择**：对规范化后的消息历史（截至最后一个 assistant 轮次）、模型名与 API Key
  作用域做 SHA-256 签名，据此匹配并续接已有的网页会话；会话创建加锁，避免重复建会话。
- **总结并滚动上下文**：当累计上下文接近实测记忆上限（约 393K 输入 token）时，
  先在临时会话里让模型产出一份精简交接摘要，再开启全新网页会话，用「摘要 + 最新用户消息」播种。
  最新的工具调用/结果会被保留（受 `DEEPSEEKER_MAX_TOOL_RESULT_TOKENS` 与更严格的
  `DEEPSEEKER_PER_TOOL_RESULT_TOKENS` 限制）；附件在摘要中以文字描述而非转发。
  首轮对话无论多大都不会触发滚动（首轮路径可接受约 1M token）。
  `/v1/models` 中声明的 `context_window`、`max_output_tokens` 反映的是这种实测行为，
  并非上游保证的硬上限。
- **完整历史注入**：当会话签名不在库中、或账号发生故障转移时，把完整对话历史注入新会话。
- **限流自动恢复**：遇到 HTTP 401/403/429 时自动把令牌标记为 `RATE_LIMITED`，
  配置新令牌、迁移完整上下文（含文件），并在一次重试内无缝续聊。
- **长上下文韧性**：上游会话失败或返回空响应（如上下文溢出）时，丢弃损坏会话，
  用压缩并限制 token 的历史注入在新会话上重试一次；不可恢复的上游错误以规范 JSON 错误返回，
  而不是裸 500。
- **工具调用与流式**：SSE 流式输出，跨分片重组 think 标签（不截断）；
  多格式工具调用解析（DSML XML、`<tool_call>` XML、`<function_call>` 块、JSON），
  统一转换为 OpenAI / Anthropic 工具结构。
- **文件与视觉**：Base64 / URL 图片提取（含 SSRF 防护）与文档流式上传。
  图片与文档通过 `ref_file_ids` 直接引用。
- **Claude 桌面版兼容**：丰富的 `/v1/models` 能力元数据 + `anthropic/claude-v4.1flash` 别名，
  支持客户端自动发现。
- **加固的控制台**：会话 TTL、登录暴力破解锁定（5 次失败 → 锁定 5 分钟）、CSRF 来源校验。

---

## 价格（每 1M token，2026-09-06）

默认模型采用统一高峰时段费率：

| 模型档位 | 输入成本（缓存未命中） | 输出成本 |
|---|---|---|
| **DeepSeek V4.1 Flash**（`v4.1flash`） | $0.44 | $1.32 |

API 响应中上报的 `cost` 即基于上述费率计算。

---

## 更新日志（本分支）

本仓库在原始项目基础上做了界面中文化与工程优化：

### 界面

- 登录页、控制台、导航、按钮、提示语、确认弹窗**全部中文化**（`lang="zh-CN"`）。
- 控制台新增**令牌池统计卡片**：令牌总数 / 可用 / 限流冷却中 / 会话记录。
- 控制台新增**服务状态面板**与**API 接入信息区**，Base URL、模型名、API Key 均可一键复制；
  API Key 默认脱敏，可点击「显示」展开。
- 令牌列表新增**最近使用时间**与限流冷却提示，删除前有中文二次确认。
- 新增 **亮色 / 暗色双主题**（跟随系统，可手动切换并记忆），完整**响应式适配**，
  新增站点图标 favicon。
- 新增**操作反馈**：添加 / 删除令牌后给出明确提示，不再静默跳转。

### 客户端身份轮换（对应 B11）

- 原版所有令牌共用一套 Android 请求头，多个账号在风控眼里是同一台设备。现在
  每个令牌按自身内容**稳定派生**身份：同一令牌永远同一台设备，不同令牌尽量不同。
- `DEEPSEEKER_IDENTITY_ROTATION` 三档：`off`（原版行为）/ `device`（默认，只轮换
  UA 与时区）/ `full`（连版本与语言一起轮换）。默认选 `device`，因为上游会校验
  客户端版本号，而 UA 只是描述性字段。
- 候选池可配置：`DEEPSEEKER_USER_AGENT_POOL`、`DEEPSEEKER_TIMEZONE_POOL`、
  `DEEPSEEKER_CLIENT_VERSION_POOL`；`DEEPSEEKER_IDENTITY_SEED` 用于重新分配。
- 控制台令牌列表新增**「客户端身份」列**，轮换结果可直接肉眼核对。
- 单令牌软并发上限默认值 **8 → 2**：上限是「软」的，8 会让小池子把突发流量
  全压在一个账号上。
- 详细的风险边界说明见上文 [关于封号](#关于封号) —— 这一项只降低多账号之间的
  相关性，不能保证不被封号。

### 工程

- `requirements.txt` 移除 `playwright`（默认路径已不需要），改为可选的
  `requirements-playwright.txt`；依赖加上版本区间约束。**镜像体积与构建时间显著下降。**
- `Dockerfile` 去掉 apt/curl 层，改用标准库健康探针 `healthcheck.py`；依赖层独立缓存。
- 新增 `docker-entrypoint.py`：容器以 root 进入，自动把数据卷属主修正为
  uid 10001 后 `setuid` 降权运行。**从旧版本升级不再需要手动 `chown` 数据卷**，
  也无需知道 uid 是多少；只读挂载可用 `DEEPSEEKER_SKIP_CHOWN=1` 跳过。
  用纯 Python 实现而非 shell，避免 `setpriv`/`gosu` 依赖与 Windows CRLF 破坏 shebang。
- 新增 `.dockerignore` 规则，测试与文档不再进入镜像。
- 新增 `get_token_stats()`；`get_tokens()` 额外返回 `last_used` / `rate_limited_until`。
- 控制台表单的「备注」字段复用日志安全清洗函数，防止伪造日志行。
- `.env.example` 补充完整中文注释；`DEEPSEEKER_API_KEY` 默认留空以自动生成强密钥。

### HTTPS 监听

- 新增 `tls_helper.py`：自动生成自签证书（EC P-256，10 年有效期，SAN 覆盖
  回环地址、本机主机名与默认网卡 IP），也支持指向自备证书；启动时校验配对与
  有效期，配置错误直接失败并说明原因。
- `DEEPSEEKER_HTTPS_ENABLED` / `DEEPSEEKER_HTTPS_PORT` / `DEEPSEEKER_HTTPS_ONLY` /
  `DEEPSEEKER_HTTPS_CERT` / `DEEPSEEKER_HTTPS_KEY` / `DEEPSEEKER_HTTPS_SAN` 一组
  环境变量；**默认关闭**，不影响既有部署。
- HTTP 与 HTTPS 在**同一个事件循环**里并行监听（会话锁是模块级 `asyncio.Lock`，
  跨事件循环会直接抛错），退出信号统一处理，一次 Ctrl+C 两个监听器一起退出。
- 控制台新增「HTTPS 接入」栏：地址、证书路径、类型、到期时间与覆盖的地址，
  自签时给出导入信任库的提示。
- 自签证书参数（SAN / 有效期）变化时才会重新生成，否则复用 —— 避免无谓地
  让用户重新导入一次信任。

---

## 免责声明

本项目仅供学习研究使用，与 DeepSeek 官方无任何隶属、背书或赞助关系。
请遵守 DeepSeek 的服务条款，合理使用。

## 贡献者

[![Contributors](https://contrib.rocks/image?repo=amancode22/deeperseeker)](https://github.com/amancode22/deeperseeker/graphs/contributors)
