#!/usr/bin/env python3
"""幂等地把 handle_path /intent-platform/* 插入 Caddy :80 站点、Catch-all handle 之前。已存在则不改。

退出码：0 已存在，1 已插入，2 失败。
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

BLOCK = """    # intent-platform 开放接口（按 IP 访问，供无法解析 sslip.io 的环境使用）
    handle_path /intent-platform/* {
        reverse_proxy intent-platform:8000
    }
"""


def main(path: Path) -> int:
    t = path.read_text(encoding='utf-8')
    if "handle_path /intent-platform" in t:
        print("caddy: /intent-platform already present")
        return 0
    idx80 = t.find(":80 {")
    if idx80 < 0:
        print("caddy: no :80 site", file=sys.stderr)
        return 2
    idx = t.find("\n    handle {", idx80)
    if idx < 0:
        print("caddy: no catch-all handle in :80 site", file=sys.stderr)
        return 2
    bak = path.with_name(path.name + ".bak." + datetime.now().strftime("%Y%m%d%H%M%S"))
    shutil.copy2(path, bak)
    # 原地写入以保持 bind-mount inode：先写临时再读回覆盖
    new = t[: idx + 1] + BLOCK + t[idx + 1 :]
    path.write_text(new, encoding='utf-8')
    print(f"caddy: inserted /intent-platform handle_path (backup {bak})")
    return 1  # changed


if __name__ == "__main__":
    try:
        sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/moonclaw/config/Caddyfile")))
    except Exception as e:
        print(f"caddy: {e}", file=sys.stderr)
        sys.exit(2)
