# Security Policy

## Supported version

安全修复只应用于 `main` 分支的最新版本。

## Reporting a vulnerability

请通过 GitHub Security Advisory 私下报告安全问题。不要在公开 Issue 中附带真实域名、访问密码、拉取令牌、SMTP 授权码或用户文件。

## Secrets

仓库只应包含 `.env.example`。部署时使用的 `.env` 必须留在服务器，并保持最小读取权限。

如果凭据曾被提交到 Git，请立即轮换凭据。仅删除当前文件不足以清除 Git 历史。
