#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -f .env ]]; then
  echo '请先完成快速启动，当前目录缺少 .env。' >&2
  exit 1
fi

# Preserve every existing iPad/SMTP setting. Only create a separate PC token.
python3 - <<'PY'
from pathlib import Path
import re
import secrets
import shutil
import time

path = Path('.env')
text = path.read_text(encoding='utf-8')
match = re.search(r'^PC_PULL_TOKEN[ \t]*=[ \t]*(.*?)[ \t]*$', text, re.M)
value = match.group(1).strip('\'" ') if match else ''
if not value or value.startswith(('replace-', '#')):
    shutil.copy2(path, path.with_name('.env.backup.pc.' + time.strftime('%Y%m%d-%H%M%S')))
    setting = 'PC_PULL_TOKEN=' + secrets.token_hex(32)
    text = text[:match.start()] + setting + text[match.end():] if match else text.rstrip() + '\n' + setting + '\n'
    temporary = path.with_name('.env.pc-tmp')
    temporary.touch(mode=0o600, exist_ok=False)
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)
PY

docker compose up -d --build
echo '电脑功能已启用。下面的令牌只填写到电脑接收程序，请勿公开：'
docker compose exec -T drop python -c 'import os; print("电脑分享入口：" + os.environ["PUBLIC_BASE_URL"].rstrip("/") + "/pc/"); print("PC_PULL_TOKEN：" + os.environ["PC_PULL_TOKEN"])'
