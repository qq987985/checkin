# 签到 · Checkin

一个可以在网页中配置的多站点自动签到工具。无需把 Cookie 写进 Docker Compose：启动容器后，在管理页面填写签到接口、登录凭据、每日次数与时间即可。

- **仓库**：[qq987985/checkin](https://github.com/qq987985/checkin)
- **镜像**：`ghcr.io/qq987985/checkin:latest`，固定版本 `ghcr.io/qq987985/checkin:2.2.1`
- **平台**：Linux amd64 / arm64，适用于对应架构的服务器、NAS 和 Docker Desktop。
- **运行依赖**：Python 3.10+，CookieCloud 本机 AES 解密使用 PyCryptodome（Docker 镜像已内置）。

> 这是通用 HTTP 签到工具，并非所有平台的开箱即用适配合集。金山灵犀 / WPS、豆包 App 等都需要填写自己的实际签到请求。当前没有经过真实验证的豆包 App 签到适配器，不处理动态签名、设备校验或验证码。

## 功能

- 多站点管理，每个站点独立启用、暂停、编辑、删除或手动执行。
- 支持 Cookie、Bearer Token、自定义 API Key 请求头和自定义请求头验证。
- 支持 GET / POST / PUT / PATCH、JSON 和原始文本请求体。
- 每日成功次数可设为 1–50，支持多个每日执行时间。
- 成功记录持久化，容器重启后继续计算当天次数。
- 支持自定义成功字段、期望值和结果关键词；未知响应不会当作成功。
- 最近 200 条运行记录，可选 Bark 通知。
- 中文管理页面，内置签到地址获取说明，适配桌面与窄屏。

## Docker Compose 部署（账号密码登录）

需要 Docker 和 Docker Compose v2。Cookie、签到 Token、接口、次数和时间仍在网页登录后填写。

```bash
mkdir -p checkin
cd checkin
curl -fsSLO https://raw.githubusercontent.com/qq987985/checkin/main/docker-compose.yml
curl -fsSLo .env https://raw.githubusercontent.com/qq987985/checkin/main/.env.example
```

编辑 `.env`：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请替换为你自己的至少8位强密码
COOKIE_SECURE=true
PUBLIC_URL=https://checkin.example.com
BIND_ADDRESS=127.0.0.1
WEB_PORT=8505
```

`ADMIN_USERNAME` / `ADMIN_PASSWORD` 是本工具的管理账号密码，不是签到网站的账号密码。请自行设置密码，不存在内置默认密码。`.env` 为本机部署配置，不提交到仓库；密码含 `$`、`#` 等字符时，按 Compose `.env` 语法使用单引号包裹。建议使用密码管理器生成长随机密码。

```bash
docker compose pull
docker compose up -d
docker compose ps
```

### Lucky HTTPS 反向代理

使用独立子域名，例如 `checkin.example.com`，从域名根路径 `/` 代理整个服务。当前不支持挂到 `/checkin/` 等子路径。

| Lucky 配置 | 填写方式 |
| --- | --- |
| Web 服务 | 创建 HTTPS 服务，选择对应域名证书 |
| 子规则类型 | 反向代理 |
| 前端域名 | 自己的域名，如 `checkin.example.com` |
| 后端目标地址 | Lucky 与签到同机且可访问宿主机回环时填 `http://127.0.0.1:8505` |
| 其他部署方式 | Lucky 在另一台设备或桥接容器内时，填可达的签到主机内网 IP，如 `http://192.168.1.10:8505`；同时将 `.env` 的 `BIND_ADDRESS` 改为该主机内网 IP |

将 `.env` 的 `PUBLIC_URL` 设置为实际访问的 HTTPS 地址（包括非标准端口，不含末尾 `/`），保留 `COOKIE_SECURE=true`，重建容器后，通过该 HTTPS 地址打开页面并输入账号密码。Lucky 终止 HTTPS，后端继续使用 HTTP 即可；无需 WebSocket 配置。保留 Cookie、Set-Cookie 和浏览器 Origin 请求头，不要给登录/API 接口添加缓存。

公网只开放 Lucky 的 HTTPS 入口；后端 8505 端口限制为本机或 Lucky 的内网地址访问。不同设备上的 Lucky 无法通过自己的 `127.0.0.1` 访问签到主机，必须填写可达内网地址。配置项目可参考 [Lucky 官方 Web 服务说明](https://lucky666.cn/docs/modules/web/)。

**仅在本机 HTTP 测试时**，将 `COOKIE_SECURE=false`、`PUBLIC_URL=` 留空，再运行 `docker compose up -d`，通过 http://127.0.0.1:8505 登录。开启 Secure 时请从 HTTPS 域名访问，不要用内网 HTTP 地址排查登录状态。

### 登录会话

- 密码只在登录时提交，服务进程用带随机盐的 PBKDF2 摘要校验；不把管理密码写入签到配置或运行日志。
- 会话保存在服务端内存，有效期 12 小时；Cookie 使用 HttpOnly、SameSite=Strict，HTTPS 模式额外启用 Secure。
- 支持退出登录；过期或重启服务后需要重新登录。
- 5 分钟内连续失败 5 次会限制后续登录。为避免信任伪造的代理头，按直连地址限制，经过 Lucky 的访问可能共享同一个限制。
- 启用账号后不再接受旧 `ADMIN_TOKEN` 绕过登录。修改环境变量里的账号密码后，重建容器生效，旧会话同时失效。
- 仍为单用户管理工具，不提供多用户权限分级。

### 完整 docker-compose.yml

```yaml
name: checkin

services:
  checkin:
    image: ghcr.io/qq987985/checkin:latest
    container_name: checkin
    restart: unless-stopped
    ports:
      # 默认仅允许本机访问；远程服务器可通过 SSH 转发或 HTTPS 反向代理访问。
      - "${BIND_ADDRESS:-127.0.0.1}:${WEB_PORT:-8505}:8505"
    environment:
      TZ: Asia/Shanghai
      HOST: 0.0.0.0
      PORT: "8505"
      ADMIN_USERNAME: ${ADMIN_USERNAME:?请先在.env中设置管理账号}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:?请先在.env中设置至少8位的管理密码}
      COOKIE_SECURE: ${COOKIE_SECURE:-true}
      PUBLIC_URL: ${PUBLIC_URL:-}
    volumes:
      # Cookie、Token、站点配置与签到记录均保存于此，升级时保留该目录。
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8505/', timeout=5).close()"]
      interval: 30s
      timeout: 10s
      start_period: 10s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "5m"
        max-file: "3"
```

## CookieCloud 自动获取 Cookie

浏览器安装 CookieCloud 扩展并配置好上传服务器后，可以让签到工具在每次执行前自动获取对应站点的最新 Cookie。

1. 在本工具页面顶部点击 **CookieCloud 设置**。
2. 填入与扩展完全相同的 **服务器地址**、**用户 KEY · UUID**、**端对端加密密码**。服务器地址包含自定义路径时保留该路径，如 `https://cookiecloud.example.com/cookiecloud`，不要追加 `/get/UUID`。
3. 加密算法通常保持「自动识别」，支持扩展的 **CryptoJS（动态 IV）** 和 **AES-128-CBC（固定 IV）**。必要时可手动选择。
4. 扩展工作模式选择「上传到服务器」，浏览器登录目标网站后点击「手动同步」。
5. 在本工具点击「测试连接」，确认下载和本机解密正常。测试只返回域名分组数量，不返回 Cookie 明文，也不发送签到请求；测试不会保存未提交的设置。
6. 点击「保存设置」。打开需要自动同步的站点，将验证方式选为 **CookieCloud（签到前自动获取 Cookie）**，保存站点即可。

CookieCloud 配置全局保存一次，每个站点单独选择是否使用。每次实际签到前下载最新密文并在本机解密，端对端密码不发送到 CookieCloud 服务器；不会自动导入浏览器扩展配置，需要在本页手动填写上述三项。UUID 和密码在页面中不回显，编辑时留空保留，支持清除配置。

匹配严格依据**签到接口 URL**：主机专属 Cookie 仅匹配该主机；域 Cookie 可匹配其子域，同时检查路径、HTTPS 和过期时间。不会将其他网站 Cookie 混入；不匹配的 Cookie 会被过滤，暂不支持需要浏览器上下文的分区 Cookie。全局获取失败、解密失败或找不到有效 Cookie 时，该次签到记为失败，不回退使用旧 Cookie。选择 CookieCloud 时会替换手工设置的 Cookie 请求头，其他不同名称的自定义请求头仍可使用。

凭据保存在本机 `data/config.json`，与其他签到凭据一样按敏感数据备份。HTTP 服务器地址可用于既有部署，但建议使用 HTTPS。Cookie 过期后仍需在浏览器重新登录并上传；浏览器没有同步到的 Cookie、手机 App 凭据、签到接口 URL、请求方法、动态签名等不会自动获取。额外 Bearer / API Key 可在自定义请求头中配置，不从 LocalStorage 自动提取。

算法对接参考 [CookieCloud 官方 Python 示例](https://github.com/easychen/CookieCloud/blob/main/examples/decrypt.py)。

## 网页配置签到

点击「添加签到站点」，填写以下内容并保存：

| 配置项 | 填写方式 |
| --- | --- |
| 站点名称 | 自己容易识别的名称，如金山灵犀 |
| 请求方法 | 按实际请求选择 GET / POST / PUT / PATCH |
| 签到接口 URL | 实际执行签到的接口，不能填写首页或签到页面地址 |
| 验证方式 | 手动 Cookie、CookieCloud 自动 Cookie、Bearer Token、API Key 或自定义请求头 |
| 验证凭据 | 自己账号的 Cookie / Token；Bearer Token 不包含 `Bearer ` 前缀 |
| API Key 请求头名称 | 按接口填写，例如 `X-API-Key`、`token` |
| 每日成功签到次数 | 1–50，达到目标后停止当天执行 |
| 每日执行时间 | 例如 `08:30,12:30,20:30`，使用容器时区（默认北京时间） |
| 高级配置 | 自定义请求头、请求体、成功字段路径及结果关键词 |

保存不会立即发送请求；点击「立即签到」会实际执行一次。暂停只关闭自动执行，仍可手动签到。

### 签到地址在哪里获取？

网页中点击输入框下方的「签到地址和请求方法在哪里获取？」可查看说明。

**签到地址就是 `Request URL`，不是浏览器地址栏中的网址。完整步骤如下：**

1. **登录并打开签到页面：**在电脑浏览器登录自己的账号，进入能手动点击「签到」的页面。先不要点击签到。
2. **打开开发者工具：**按 F12（Mac 可按 ⌥⌘I），也可右键网页选择「检查」，打开 Network（网络）面板。
3. **准备记录请求：**选择 Fetch/XHR，点击网络面板的清除按钮，清空已有记录；保持面板打开。若签到会跳转页面，可勾选 Preserve log（保留日志）。
4. **手动签到一次：**回到页面点击「签到」，查看网络面板刚新增的请求。没有看到请求时，切回 All（全部）重新查看。
5. **确认真正的签到请求：**逐条查看新请求的 Response（响应）或 Preview（预览），找到返回签到结果的那一条。checkin、sign 等名称只作线索；不要选图片、统计上报或 OPTIONS 预检请求。
6. **复制签到地址 URL：**打开这条请求的 Headers（请求头）→ General（常规），找到 Request URL，复制完整地址（包括 ? 后的查询参数），粘贴到本工具的「签到接口 URL」。不要复制浏览器地址栏，也不要只复制请求列表里的短名称。
7. **选择对应的请求方法：**在同一条请求的 General（常规）里查看 Request Method：显示 POST 就选择 POST，显示 GET 就选择 GET，PUT / PATCH 同理。URL 和方法必须来自同一条请求，不能根据网址猜测。
8. **填写验证凭据：**在 Request Headers（请求标头）中查看：Cookie 填到 Cookie 验证；Authorization: Bearer 后面的值填到 Bearer Token 验证（不含 Bearer 前缀）；其他 API Key 按原来的请求头名称和值填写。需要多个验证请求头时，在高级配置里填写 JSON 对象。
9. **核对请求体和结果规则：**如原请求有 Payload（请求载荷），按实际格式填写高级配置里的请求体，并核对 Content-Type；再根据实际响应设置成功字段或关键词。没有请求体就选择「无请求体」。
10. **保存并确认执行结果：**设置每日次数与时间后保存。可点击「立即签到」测试，这会实际发送签到请求；若当天已手动签到，可能返回「已签到」。检查运行记录；「待确认」时先核对响应规则，不要直接当作成功。

**豆包 App：**需要从手机 App 实际签到时的请求取得 URL、请求头和请求体。火山方舟模型 API Key 不等于豆包 App 登录凭据。需要动态签名、设备参数或验证码的接口不能仅靠静态 Token 配置完成。

完整 cURL、Cookie 和 Token 可能包含账号凭据，请直接在自己的管理页面填写，不要提交到公开仓库。

### 验证方式怎么判断？

先登录目标网站 → Network（网络）清空记录 → 手动签到一次 → 找到实际签到请求 → Headers（请求头）→ **Request Headers（请求标头）**，按原请求填写。不要把 Response Headers（响应头）当作请求凭据。

| 实际请求中的内容 | 工具里如何填写 |
| --- | --- |
| `Cookie: session=…; other=…` | 选择 Cookie，填冒号后完整值，包括键值对和分号；不含 `Cookie:`，也不要复制响应的 Set-Cookie |
| `Authorization: Bearer abc…` | 选择 Bearer Token，只填 `abc…`，不含 `Bearer ` 前缀 |
| `X-API-Key: abc…` 或 `token: abc…` | 选择 API Key，请求头名称和值均照原请求填写 |
| `Authorization: Basic …` 等其他方案 | 选择「无需验证 / 使用自定义请求头」，在高级配置里填写完整请求头名称和值 |
| 同时需要多个验证头 | 在高级配置中填写完整 JSON；或选择一种验证方式并补充其他不同名称的请求头。同名请求头以选定验证方式生成的值为准 |
| 请求头里没有凭据 | 继续检查 URL 查询参数、Payload 和接口文档，不能直接认定无需验证 |

自定义请求头结构示例（仅演示）：

```json
{"Cookie":"session=你的值","Authorization":"Basic 你的值"}
```

如果凭据在 URL 查询参数里，保留完整 URL；如果在请求体里，照原格式填写请求体并核对 Content-Type。部分接口同时依赖 URL、请求头和请求体，不能只填其中一项。「无需验证 / 使用自定义请求头」也可用于手工配置认证，不表示接口一定免登录。

保存后可点击「立即签到」真实执行并检查运行记录。401 / 403 可能与认证、权限、签名或设备校验有关，不能仅凭状态码确定原因；200 也不代表业务成功。按实际响应配置成功规则，「待确认」时人工核对。凭据过期后从新请求重新获取。

豆包 App 等接口若涉及动态签名、设备校验、一次性参数或验证码，复制静态 Token 可能仍无法执行。Cookie、Token、含凭据的 URL 和完整 cURL 请只在自己的管理页面填写，避免公开；编辑已有凭据时留空会保留原值。

### 次数与时间如何配合？

例如目标每天 **2 次**，时间设置 `08:30,12:30,20:30`：

- 每个配置时间点执行一次。
- 前两个时间成功，第三个时间自动跳过。
- 失败、网络异常、未知结果不计成功，后续时间仍会尝试。
- 若目标次数多于时间点，需要手动补足。
- 当天达到目标后，「立即签到」也会跳过；次日重新计数。
- 编辑配置保留当天次数；重启不补跑错过的时间，电脑休眠或容器停机期间不会执行。

站点响应“已签到”是否计成功，由配置规则决定，不代表获得了额外奖励。

### 如何判定成功？

失败判定优先：HTTP 非 2xx、失败关键词、JSON `success: false` 或非零 `error` 均判定失败。

配置成功字段时，例如路径 `data.signed`、期望值 `true`，必须精确匹配，JSON 类型也需要一致。支持数组下标，如 `data.0.code`。

未配置字段时，按成功关键词或 JSON `success: true` / `error: 0` 判断。没有明确匹配的响应显示「待确认」，不累计次数。建议按实际接口设置成功字段，减少关键词误判。

## 通知与数据

「通知设置」可填写完整 Bark 推送地址。推送仅包含站点名称、通用结果和 HTTP 状态，不转发接口响应原文；推送失败不会改变签到结果。

| 文件 | 内容 |
| --- | --- |
| `data/config.json` | 站点配置、Cookie / Token、Bark 地址 |
| `data/state.json` | 当日成功次数、定时执行记录、最近 200 条日志 |

凭据以**明文保存在部署机器**，配置文件权限为 `600`。管理页面不回显已保存凭据、自定义请求头和请求体；编辑时留空保留原值。接口 URL 可能含查询参数凭据，仅向已认证页面返回。运行日志不保存响应原文。

升级、重建容器时保留 `data/`，不要把它推送到 GitHub。可停机后备份 `.env` 与 `data/`，恢复时放回原部署目录再启动。

从 2.0 升级到 2.1 时，更新 Compose 和 `.env`，将旧 `ADMIN_TOKEN` 换为账号密码、域名及 Cookie 设置，保留原 `data/`。

升级至 2.2.0 后，容器内外默认端口均改为 `8505`。如已有 `.env`，请将 `WEB_PORT` 改为 `8505`；Lucky 后端目标地址也同步改为 `http://签到主机地址:8505`。

## 更新和常用命令

```bash
# 更新镜像并重建容器，保留 data/
docker compose pull
docker compose up -d

# 查看运行情况和日志
docker compose ps
docker compose logs -f --tail=100 checkin

# 停止（保留配置和数据）
docker compose down
```

如需固定版本，将镜像标签 `latest` 改为 `2.2.1`。

### 从 Lingxi-Check-in 迁移

1. 在原部署目录执行旧配置的 `docker compose down`。
2. 备份并保留原 `data/` 目录。
3. 替换为新 Compose 和 `.env`，填写新的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 与反代域名配置。
4. 运行 `docker compose pull && docker compose up -d`，打开管理页添加灵犀站点。

首次启动、尚无 `config.json` 时，可以导入旧 `CHECKIN_URL`、`LINGXI_COOKIE`、`CHECKIN_TIMES`、`BARK_URL` 环境变量；如需自动导入，请仅在首次启动时临时加入这些变量。导入的灵犀站点同时兼容 `data/last_success.txt`。之后以网页保存配置为准。旧镜像不会自动改名或删除。

## 本地开发

Python 3.10+：

```bash
git clone https://github.com/qq987985/checkin.git
cd checkin
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
TZ=Asia/Shanghai .venv/bin/python checkin.py
```

打开 http://127.0.0.1:8505 。本机开发默认绑定回环地址，可免登录；对外监听应设置 `ADMIN_USERNAME` 和至少 8 位 `ADMIN_PASSWORD`。旧 `ADMIN_TOKEN` 仅在未启用账号时兼容，公网部署请迁移到账号模式。可通过 `HOST`、`PORT`、`DATA_DIR`、`TZ` 调整监听地址、端口、数据目录与时区。

本地构建容器：

```bash
docker build -t checkin:local .
```

验证：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile checkin.py cookiecloud.py
node --check static/app.js
ADMIN_USERNAME=test ADMIN_PASSWORD=compose-test-password docker compose config --quiet
```

自动测试只访问本地模拟接口。GitHub Actions 先执行测试、Compose 校验与容器启动检查，再发布 amd64 / arm64 镜像及 `latest`、版本、提交标签。

## 协议

[MIT](LICENSE)。请仅使用自己有权访问的账号和接口，并遵守目标服务规则。
