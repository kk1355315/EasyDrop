#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "错误：请使用 sudo bash deploy/setup.sh 运行。"
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "错误：无法识别操作系统。本脚本仅支持 Ubuntu/Debian。"
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
  echo "错误：本脚本仅支持 Ubuntu/Debian，当前系统为 ${PRETTY_NAME:-unknown}。"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

prompt_required() {
  local prompt="$1"
  local value=""
  while [[ -z "${value}" ]]; do
    read -r -p "${prompt}: " value
  done
  printf '%s' "${value}"
}

prompt_default() {
  local prompt="$1"
  local default="$2"
  local value=""
  read -r -p "${prompt} [${default}]: " value
  printf '%s' "${value:-$default}"
}

env_quote() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1].replace("$", "$$"), ensure_ascii=False))' "$1"
}

echo
echo "========== EasyDrop 一键部署 =========="
echo "带“必须”的项目不能为空。输入内容只写入本机 .env，不会上传到 GitHub。"
echo

if [[ -f .env ]]; then
  echo "警告：当前目录已经存在 .env。继续会生成新的登录密码和 Token。"
  read -r -p '确认重新配置请输入 YES: ' RECONFIGURE
  if [[ "${RECONFIGURE}" != "YES" ]]; then
    echo "已取消，没有修改现有配置。"
    exit 0
  fi
  cp -a .env ".env.backup.$(date +%Y%m%d-%H%M%S)"
fi

DOMAIN="$(prompt_required '【必须】公网域名（例：drop.example.com，不要写 https:// 和 /drop）')"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"
if [[ ! "${DOMAIN}" =~ ^[A-Za-z0-9.-]+$ || "${DOMAIN}" != *.* ]]; then
  echo "错误：域名格式不正确：${DOMAIN}"
  exit 1
fi

CERT_EMAIL="$(prompt_required '【必须】HTTPS 证书通知邮箱')"
SMTP_HOST="$(prompt_required '【必须】SMTP 服务器（例：smtp.example.com）')"
SMTP_PORT="$(prompt_default '【必须】SMTP 端口' '465')"
if [[ ! "${SMTP_PORT}" =~ ^[0-9]+$ || "${SMTP_PORT}" -lt 1 || "${SMTP_PORT}" -gt 65535 ]]; then
  echo "错误：SMTP 端口必须是 1 到 65535 之间的数字。"
  exit 1
fi

echo "SMTP 加密方式："
echo "  1) SSL/TLS（常见端口 465）"
echo "  2) STARTTLS（常见端口 587）"
SMTP_MODE="$(prompt_default '请选择 1 或 2' '1')"
if [[ "${SMTP_MODE}" == "1" ]]; then
  SMTP_SSL="true"
elif [[ "${SMTP_MODE}" == "2" ]]; then
  SMTP_SSL="false"
else
  echo "错误：SMTP 加密方式只能填写 1 或 2。"
  exit 1
fi

SMTP_USER="$(prompt_required '【必须】SMTP 登录账号（通常是完整邮箱）')"
read -r -s -p '【必须】SMTP 密码或授权码（输入时不会显示）: ' SMTP_PASSWORD
echo
if [[ -z "${SMTP_PASSWORD}" ]]; then
  echo "错误：SMTP 密码或授权码不能为空。"
  exit 1
fi
SMTP_FROM="$(prompt_default '发件人邮箱' "${SMTP_USER}")"
TRIGGER_TO="$(prompt_required '【必须】已登录 iPad“邮件”App 的 iCloud 邮箱')"
TRIGGER_SUBJECT="$(prompt_default '触发邮件主题' 'DROP_READY')"
RETENTION_HOURS="$(prompt_default '未接收文件保留小时数' '48')"
if [[ ! "${RETENTION_HOURS}" =~ ^[0-9]+$ || "${RETENTION_HOURS}" -lt 1 ]]; then
  echo "错误：保留小时数必须是正整数。"
  exit 1
fi

read -r -s -p 'PWA 登录密码（直接回车将自动生成）: ' LOGIN_PASSWORD
echo

echo
echo "[1/6] 安装 Docker、Nginx 和 Certbot……"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl nginx certbot python3 python3-certbot-nginx openssl

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "${ID}" "${VERSION_CODENAME}" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker nginx

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 'Nginx Full'
fi

if ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "${ID}" "${VERSION_CODENAME}" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-compose-plugin
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "错误：Docker Compose 安装失败。"
  exit 1
fi

LOGIN_PASSWORD="${LOGIN_PASSWORD:-$(openssl rand -hex 16)}"
SESSION_SECRET="$(openssl rand -hex 32)"
PULL_TOKEN="$(openssl rand -hex 24)"

echo "[2/6] 写入私有配置……"
umask 077
{
  printf 'BASE_PATH=%s\n' "$(env_quote '/drop')"
  printf 'PUBLIC_BASE_URL=%s\n' "$(env_quote "https://${DOMAIN}/drop")"
  printf 'LOGIN_PASSWORD=%s\n' "$(env_quote "${LOGIN_PASSWORD}")"
  printf 'SESSION_SECRET=%s\n' "$(env_quote "${SESSION_SECRET}")"
  printf 'PULL_TOKEN=%s\n' "$(env_quote "${PULL_TOKEN}")"
  printf 'MAX_FILE_BYTES=%s\n' "$(env_quote '2147483648')"
  printf 'RETENTION_HOURS=%s\n' "$(env_quote "${RETENTION_HOURS}")"
  printf 'SMTP_HOST=%s\n' "$(env_quote "${SMTP_HOST}")"
  printf 'SMTP_PORT=%s\n' "$(env_quote "${SMTP_PORT}")"
  printf 'SMTP_SSL=%s\n' "$(env_quote "${SMTP_SSL}")"
  printf 'SMTP_USER=%s\n' "$(env_quote "${SMTP_USER}")"
  printf 'SMTP_PASSWORD=%s\n' "$(env_quote "${SMTP_PASSWORD}")"
  printf 'SMTP_FROM=%s\n' "$(env_quote "${SMTP_FROM}")"
  printf 'TRIGGER_TO=%s\n' "$(env_quote "${TRIGGER_TO}")"
  printf 'TRIGGER_SUBJECT=%s\n' "$(env_quote "${TRIGGER_SUBJECT}")"
} > .env
chmod 600 .env

echo "[3/6] 构建并启动 EasyDrop……"
mkdir -p data
docker compose build
docker compose run --rm --user root drop \
  sh -c 'mkdir -p /data/files && chown -R app:app /data'
docker compose up -d
echo "正在发送一封 SMTP 测试邮件……"
if ! docker compose exec -T drop python -c \
  'from app.main import send_trigger_email; send_trigger_email(1, ["EasyDrop deployment test"]); print("SMTP OK")'; then
  echo "错误：SMTP 测试失败。请检查服务器地址、端口、加密方式、账号和授权码。"
  exit 1
fi

echo "[4/6] 配置 Nginx……"
NGINX_SITE="/etc/nginx/sites-available/easydrop"
if [[ -f "${NGINX_SITE}" ]]; then
  cp -a "${NGINX_SITE}" "${NGINX_SITE}.backup.$(date +%Y%m%d-%H%M%S)"
fi
cat > "${NGINX_SITE}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 2g;

    location = /drop {
        return 301 /drop/;
    }

    location ^~ /drop/ {
        proxy_pass http://127.0.0.1:8012;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location / {
        return 404;
    }
}
EOF
ln -sfn "${NGINX_SITE}" /etc/nginx/sites-enabled/easydrop
nginx -t
systemctl reload nginx

echo "[5/6] 申请 HTTPS 证书……"
echo "如果此步失败，请确认域名已经解析到本服务器，并且安全组开放 80/443 端口。"
certbot --nginx --non-interactive --agree-tos --redirect \
  --email "${CERT_EMAIL}" -d "${DOMAIN}"

echo "[6/6] 检查服务……"
for _ in {1..30}; do
  if curl -fsS "https://${DOMAIN}/drop/" >/dev/null; then
    break
  fi
  sleep 2
done
curl -fsS "https://${DOMAIN}/drop/" >/dev/null
docker compose ps

echo
echo "========== 部署完成 =========="
echo "PWA 地址：https://${DOMAIN}/drop/"
echo "PWA 登录密码：${LOGIN_PASSWORD}"
echo "iPad 快捷指令服务器地址：https://${DOMAIN}"
echo "iPad PULL_TOKEN：${PULL_TOKEN}"
echo
echo "请立即把上面两项密码保存到安全位置。"
echo "查看日志：cd ${PROJECT_DIR} && sudo docker compose logs -f"
