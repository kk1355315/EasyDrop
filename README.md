# EasyDrop

一个轻量、自托管的文件投递服务。

在 Android 上安装 PWA 后，它会出现在系统分享菜单中。文件上传到自己的服务器后，服务器发送一封触发邮件。iPad 的“快捷指令”自动化收到邮件，随后下载文件：照片和视频进入“照片”，其他文件进入 iCloud Drive。

```text
Android 分享菜单 → PWA → FastAPI → 触发邮件
                                  ↓
                         iPad 快捷指令拉取
```

## 功能

- Android Web Share Target，支持多文件分享
- 普通网页上传
- 密码登录和 HttpOnly Cookie
- 独立的 iPad 拉取令牌
- SQLite 持久化待处理队列
- SHA-256 文件哈希
- 下载确认后删除服务器副本
- 未接收文件自动过期
- Docker Compose 与 Nginx 示例

## 要求

- 一台安装 Docker Compose 的 Linux 服务器
- 一个支持 HTTPS 的域名
- 可通过 SMTP 发信的邮箱
- Android Chrome 或兼容 Web Share Target 的浏览器
- 支持“电子邮件”自动化的 iPhone 或 iPad

## 快速部署

复制示例配置：

```bash
cp .env.example .env
```

生成三项随机凭据：

```bash
openssl rand -hex 16   # LOGIN_PASSWORD
openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 24   # PULL_TOKEN
```

编辑 `.env`，然后启动：

```bash
mkdir -p data
docker compose build
docker compose run --rm --user root drop \
  sh -c 'mkdir -p /data/files && chown -R app:app /data'
docker compose up -d
```

把 [Nginx 示例](deploy/nginx-location.conf.example)加入现有 HTTPS 站点，检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

打开 `https://your-domain.example/drop/`。使用 Android Chrome 的“安装应用”或“添加到主屏幕”。安装后，PWA 会注册到系统分享菜单。

## iPad 快捷指令

仓库已经包含可直接导入的 Apple 签名文件：

- [接收文件新.shortcut](shortcut/接收文件新.shortcut)
- [安装与自动化说明](shortcut/README.md)

导入时只需填写服务器根地址和 `PULL_TOKEN`。无需手工重建下载、分类保存和确认删除流程。

快捷指令使用以下接口：

| 接口 | 用途 |
|---|---|
| `GET /drop/api/pending` | 获取待接收文件 |
| `GET /drop/api/file/{id}` | 下载文件 |
| `POST /drop/api/ack/{id}` | 确认保存成功并删除服务器副本 |

三个接口都要求请求头：

```http
Authorization: Bearer <PULL_TOKEN>
```

## 配置

| 变量 | 说明 |
|---|---|
| `BASE_PATH` | 部署路径，默认 `/drop`；附带快捷指令按此路径请求 |
| `PUBLIC_BASE_URL` | 外网完整地址，例如 `https://your-domain.example/drop` |
| `LOGIN_PASSWORD` | Android/PWA 登录密码 |
| `SESSION_SECRET` | Cookie 签名密钥 |
| `PULL_TOKEN` | iPad API 令牌 |
| `MAX_FILE_BYTES` | 单个文件最大字节数 |
| `RETENTION_HOURS` | 未接收文件保留小时数 |
| `SMTP_*` | SMTP 服务器和账号配置 |
| `TRIGGER_TO` | 接收触发邮件的地址 |
| `TRIGGER_SUBJECT` | 触发主题，默认 `DROP_READY` |

附带快捷指令会请求 `[服务器根地址]/drop/api/pending`。因此建议保持 `BASE_PATH=/drop`。如果修改它，也要同步修改 Nginx 和快捷指令。

## 数据与备份

运行数据位于宿主机的 `data/`：

- `data/drop.sqlite3`：队列元数据
- `data/files/`：待接收文件

备份时停止容器，再复制整个 `data/` 即可。

## 安全提示

- 不要提交 `.env`、邮箱授权码、真实令牌或 TLS 私钥。
- 服务应只通过 HTTPS 对外开放。
- 容器端口默认只绑定 `127.0.0.1:8012`。
- 收到文件并调用 `ack` 后，服务器副本会立即删除。

安全问题请参阅 [SECURITY.md](SECURITY.md)。

## 开发与测试

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Windows PowerShell 激活命令为 `.\.venv\Scripts\Activate.ps1`。

## License

[MIT](LICENSE)
