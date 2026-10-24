# 签到 · Checkin

一个可以在网页中配置的多站点自动签到工具。无需把 Cookie 写进 Docker Compose：启动容器后，在管理页面填写签到接口、登录凭据、每日次数与时间即可。

- **仓库**：[qq987985/checkin](https://github.com/qq987985/checkin)
- **镜像**：`ghcr.io/qq987985/checkin:latest`，固定版本 `ghcr.io/qq987985/checkin:2.0.0`
- **平台**：Linux amd64 / arm64，适用于对应架构的服务器、NAS 和 Docker Desktop。
- **运行依赖**：Python 标准库，无第三方 Python 依赖。

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

## Docker Compose 部署

需要已安装 Docker 和 Docker Compose v2。

### 1. 准备文件

```bash
mkdir -p checkin
cd checkin
curl -fsSLO https://raw.githubusercontent.com/qq987985/checkin/main/docker-compose.yml
curl -fsSLo .env https://raw.githubusercontent.com/qq987985/checkin/main/.env.example
```

编辑 `.env`，在 `ADMIN_TOKEN=` 后填入**至少 16 位随机字符串**。可用以下命令生成，再粘贴进去：

```bash
openssl rand -hex 24
```

`.env` 示例（请自行替换管理令牌）：

```dotenv
ADMIN_TOKEN=这里替换成你生成的随机管理令牌
BIND_ADDRESS=127.0.0.1
WEB_PORT=8765
```

`ADMIN_TOKEN` 是本工具的管理访问令牌，**不是目标网站的签到 Token**。Cookie、签到接口、API Key、时间和次数都在网页中填写，不需要写进 Compose 或 `.env`。

### 2. 启动

```bash
docker compose pull
docker compose up -d
docker compose ps
```

本机浏览器打开 **http://127.0.0.1:8765**，在「管理令牌」中输入 `.env` 设置的 `ADMIN_TOKEN`。令牌仅保存在当前标签页内存，刷新页面后需要重新输入。

### 3. 服务器 / NAS 访问

默认端口仅绑定部署机器的 `127.0.0.1`。如果部署在远程服务器，推荐在自己电脑执行 SSH 转发：

```bash
ssh -L 8765:127.0.0.1:8765 用户@服务器地址
```

然后在自己电脑打开 **http://127.0.0.1:8765**。

如果通过 NAS 局域网访问，将 `.env` 的 `BIND_ADDRESS` 改成 NAS 的局域网 IP，再执行 `docker compose up -d`，浏览器访问 `http://NAS局域网IP:8765`。请在可信局域网使用，并限制访问范围；通过公网访问时使用 HTTPS 反向代理。修改 `WEB_PORT` 可避开端口冲突。

### 完整 docker-compose.yml

```yaml
name: checkin
services:
  checkin:
    image: ghcr.io/qq987985/checkin:latest
    container_name: checkin
    restart: unless-stopped
    ports:
      - "${BIND_ADDRESS:-127.0.0.1}:${WEB_PORT:-8765}:8765"
    environment:
      TZ: Asia/Shanghai
      HOST: 0.0.0.0
      PORT: "8765"
      ADMIN_TOKEN: ${ADMIN_TOKEN:?请先在.env中设置至少16位的随机管理令牌}
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/', timeout=5).close()"]
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

## 网页配置签到

点击「添加签到站点」，填写以下内容并保存：

| 配置项 | 填写方式 |
| --- | --- |
| 站点名称 | 自己容易识别的名称，如金山灵犀 |
| 请求方法 | 按实际请求选择 GET / POST / PUT / PATCH |
| 签到接口 URL | 实际执行签到的接口，不能填写首页或签到页面地址 |
| 验证方式 | Cookie、Bearer Token、API Key 或自定义请求头 |
| 验证凭据 | 自己账号的 Cookie / Token；Bearer Token 不包含 `Bearer ` 前缀 |
| API Key 请求头名称 | 按接口填写，例如 `X-API-Key`、`token` |
| 每日成功签到次数 | 1–50，达到目标后停止当天执行 |
| 每日执行时间 | 例如 `08:30,12:30,20:30`，使用容器时区（默认北京时间） |
| 高级配置 | 自定义请求头、请求体、成功字段路径及结果关键词 |

保存不会立即发送请求；点击「立即签到」会实际执行一次。暂停只关闭自动执行，仍可手动签到。

### 签到地址在哪里获取？

网页中点击输入框下方的「签到地址在哪里获取？」可查看说明。

**网页签到：**电脑浏览器登录 → 打开开发者工具的 Network（网络）面板 → 选择 Fetch/XHR → 手动点击「签到」→ 找到对应请求 → 在 Headers 中复制 Request URL 和 Request Method。验证 Cookie / Token 位于 Request Headers，请求体位于 Payload。必要时切回 All 查看全部请求。可右键该请求选择 Copy as cURL，便于核对完整参数。

**豆包 App：**需要从手机 App 实际签到时的请求取得 URL、请求头和请求体。火山方舟模型 API Key 不等于豆包 App 登录凭据。需要动态签名、设备参数或验证码的接口不能仅靠静态 Token 配置完成。

完整 cURL、Cookie 和 Token 可能包含账号凭据，请直接在自己的管理页面填写，不要提交到公开仓库。

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

如需固定版本，将镜像标签 `latest` 改为 `2.0.0`。

### 从 Lingxi-Check-in 迁移

1. 在原部署目录执行旧配置的 `docker compose down`。
2. 备份并保留原 `data/` 目录。
3. 替换为新 Compose 和 `.env`，填写新的 `ADMIN_TOKEN`。
4. 运行 `docker compose pull && docker compose up -d`，打开管理页添加灵犀站点。

首次启动、尚无 `config.json` 时，可以导入旧 `CHECKIN_URL`、`LINGXI_COOKIE`、`CHECKIN_TIMES`、`BARK_URL` 环境变量；如需自动导入，请仅在首次启动时临时加入这些变量。导入的灵犀站点同时兼容 `data/last_success.txt`。之后以网页保存配置为准。旧镜像不会自动改名或删除。

## 本地开发

Python 3.10+：

```bash
git clone https://github.com/qq987985/checkin.git
cd checkin
TZ=Asia/Shanghai python3 checkin.py
```

打开 http://127.0.0.1:8765 。本机默认绑定回环地址；对外监听必须设置至少 16 位 `ADMIN_TOKEN`。可通过 `HOST`、`PORT`、`DATA_DIR`、`TZ` 调整监听地址、端口、数据目录与时区。

本地构建容器：

```bash
docker build -t checkin:local .
```

验证：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile checkin.py
node --check static/app.js
ADMIN_TOKEN=local-compose-check-token docker compose config --quiet
```

自动测试只访问本地模拟接口。GitHub Actions 先执行测试、Compose 校验与容器启动检查，再发布 amd64 / arm64 镜像及 `latest`、版本、提交标签。

## 协议

[MIT](LICENSE)。请仅使用自己有权访问的账号和接口，并遵守目标服务规则。
