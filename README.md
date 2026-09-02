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

## 快速启动

以下流程适用于一台新的 Ubuntu 或 Debian 服务器。脚本会自动安装 Docker、Nginx、HTTPS 证书和 EasyDrop。你不需要手工编辑 `.env` 或 Nginx 配置。

### 第一步：提前准备

开始前必须准备好这四项：

1. 一台有 `root` 或 `sudo` 权限的 Ubuntu/Debian 服务器。
2. 一个域名，例如 `drop.example.com`。
3. 在域名服务商处添加一条 **A 记录**，把域名指向服务器公网 IPv4。
4. 在云服务器安全组中放行 TCP **80** 和 **443** 端口。

还需要从发信邮箱的设置页面取得：

- SMTP 服务器地址
- SMTP 端口
- SSL/TLS 或 STARTTLS
- SMTP 登录账号
- SMTP 密码或授权码

注意：很多邮箱要求使用单独的“SMTP 授权码”，不能直接使用邮箱登录密码。

### 第二步：复制并运行

SSH 登录服务器后，完整复制下面四行：

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/kk1355315/EasyDrop.git
cd EasyDrop
sudo bash deploy/setup.sh
```

脚本会逐项提问。带 `【必须】` 的项目必须填写：

| 脚本问题 | 填写内容 | 示例 |
|---|---|---|
| `【必须】公网域名` | 只填域名，不要写 `https://`，也不要加 `/drop` | `drop.example.com` |
| `【必须】HTTPS 证书通知邮箱` | 用于接收证书过期通知 | `admin@example.com` |
| `【必须】SMTP 服务器` | 邮箱服务商给出的 SMTP 地址 | `smtp.example.com` |
| `【必须】SMTP 端口` | 通常是 `465` 或 `587` | `465` |
| `SMTP 加密方式` | 端口 465 通常选 1；端口 587 通常选 2 | `1` |
| `【必须】SMTP 登录账号` | 通常是完整邮箱地址 | `sender@example.com` |
| `【必须】SMTP 密码或授权码` | 输入时屏幕不会显示，这是正常的 | 邮箱授权码 |
| `发件人邮箱` | 通常直接回车，使用 SMTP 登录账号 | 直接回车 |
| `【必须】iPad 接收触发邮件的 iCloud 邮箱` | 已登录 iPad“邮件”App 的 iCloud 邮箱 | `你的邮箱@icloud.com` |
| `触发邮件主题` | 直接回车使用 `DROP_READY` | 直接回车 |
| `未接收文件保留小时数` | 直接回车使用 48 小时 | 直接回车 |
| `PWA 登录密码` | 可自己设置；直接回车会自动生成 | 直接回车 |

脚本完成后会显示四项结果：

```text
PWA 地址
PWA 登录密码
iPad 快捷指令服务器地址
iPad PULL_TOKEN
```

请把登录密码和 `PULL_TOKEN` 保存到密码管理器。它们不会上传到 GitHub。

### 第三步：安装 Android PWA

1. 在 Android 手机上使用 Chrome 打开脚本显示的 `PWA 地址`。
2. 输入脚本显示的 `PWA 登录密码`。
3. 打开 Chrome 菜单，选择“安装应用”或“添加到主屏幕”。
4. 安装完成后，“发到 iPad”会出现在 Android 系统分享菜单中。

### 第四步：导入 iPad 快捷指令

1. 下载并打开 [接收文件新.shortcut](shortcut/接收文件新.shortcut)。
2. “服务器地址”填写脚本显示的 `iPad 快捷指令服务器地址`。这里只填 `https://域名`，不要加 `/drop`。
3. “访问 Token”填写脚本显示的 `iPad PULL_TOKEN`。
4. 按照下方的“iPad 邮件自动化设置”创建一次自动化。

至此部署完成。Android 分享文件后，服务器会发出触发邮件，iPad 自动下载并保存。

## iPad 邮件自动化设置（必须）

`.shortcut` 文件只能包含接收文件的工作流，不能替用户创建“个人自动化”。因此，每台 iPad 都需要手工设置一次。

### 1. 第一次手动运行快捷指令

打开“快捷指令”App，运行一次 `接收文件新`。

第一次运行时，请允许它访问：

- EasyDrop 服务器
- “照片”App
- iCloud Drive

这一步要在 iPad 解锁状态下完成，避免以后锁屏自动运行时停在权限询问页面。

### 2. 创建电子邮件自动化

打开：

```text
快捷指令 → 自动化 → 右上角“+” → 电子邮件
```

按下面填写：

| 设置项 | 填写内容 |
|---|---|
| 发件人 | 留空 |
| 主题包含 | `DROP_READY` |
| 账户 | 选择上面填写的 iCloud 邮箱账户 |
| 收件人 | 留空 |
| 运行方式 | `立即运行` |

如果部署时修改了 `TRIGGER_SUBJECT`，这里必须填写完全相同的主题。

### 3. 选择运行的快捷指令

进入下一步后选择：

```text
运行快捷指令 → 接收文件新
```

确认设置中没有开启“运行前询问”，然后保存自动化。

最终流程应为：

```text
收到主题包含 DROP_READY 的邮件
              ↓
          立即运行
              ↓
         接收文件新
              ↓
图片/视频 → 照片
其他文件 → iCloud Drive/easydrop/
```

### 4. 测试完整流程

1. 保持 iPad 联网，先锁屏。
2. 在 Android 相册或文件管理器中选择一个文件。
3. 点“分享”，选择“发到 iPad”。
4. 等待触发邮件到达。
5. 解锁 iPad，检查“照片”或 `iCloud Drive/easydrop/`。

测试成功后，日常使用不需要打开“快捷指令”App，也不需要手工运行 `接收文件新`。

## 常用维护命令

进入项目目录：

```bash
cd ~/EasyDrop
```

查看运行状态：

```bash
sudo docker compose ps
```

查看日志：

```bash
sudo docker compose logs -f
```

更新到最新版：

```bash
cd ~/EasyDrop
git pull
sudo docker compose up -d --build
```

停止或重新启动：

```bash
sudo docker compose stop
sudo docker compose restart
```

## 已有 Docker/Nginx 的手动部署

熟悉服务器配置的用户可以复制 [.env.example](.env.example)，填写其中所有标有 `【必须修改】` 的项目，然后使用 [Nginx 示例](deploy/nginx-location.conf.example)：

```bash
cp .env.example .env
mkdir -p data
docker compose build
docker compose run --rm --user root drop \
  sh -c 'mkdir -p /data/files && chown -R app:app /data'
docker compose up -d
```

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
