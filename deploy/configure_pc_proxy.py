"""Add a sibling PWA location to the existing EasyDrop Nginx server block."""
import argparse
from pathlib import Path
import re
import shutil
import time


def add_pc_proxy(content: str, base_path: str = "/drop") -> str:
    pc_path = base_path + "-pc"
    if re.search(r"location\s+(?:\^~\s+)?" + re.escape(pc_path) + r"/\s*\{", content):
        return content
    match = re.search(r"(?m)^(?P<indent>[ \t]*)location\s+\^~\s+" + re.escape(base_path) + r"/\s*\{[^{}]*\}", content)
    if not match:
        raise ValueError("找不到原来的 EasyDrop location，请按 nginx-location.conf.example 手工添加电脑入口")
    clone = match.group(0).replace(base_path + "/", pc_path + "/", 1)
    indent = match.group("indent")
    redirect = f"{indent}location = {pc_path} {{\n{indent}    return 301 {pc_path}/;\n{indent}}}"
    return content[:match.end()] + "\n\n" + redirect + "\n\n" + clone + content[match.end():]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    path = args.config.resolve(strict=True)
    content = path.read_text(encoding="utf-8")
    updated = add_pc_proxy(content)
    if updated != content:
        shutil.copy2(path, str(path) + ".backup.pc." + time.strftime("%Y%m%d-%H%M%S"))
        path.write_text(updated, encoding="utf-8")
    print("Nginx PC location ready")
