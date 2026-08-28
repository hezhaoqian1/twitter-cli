#!/usr/bin/env python3
"""Batch-check X cookie TSV rows with twitter-cli's TWITTER_COOKIE_FILE flow."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CookieRow:
    """保存一行 TSV 的脱敏字段和可验证 Cookie 数据。"""

    line_number: int
    username: str
    token: str
    cookies: list[dict[str, object]]


def _decode_cookie_blob(encoded: str) -> list[dict[str, object]]:
    """解码 Base64 Cookie JSON，并确保结构可被 session 文件复用。"""
    decoded = base64.b64decode(encoded)
    data = json.loads(decoded)
    if not isinstance(data, list):
        raise ValueError("cookie blob is not a JSON list")
    return [item for item in data if isinstance(item, dict) and item.get("name")]


def parse_tsv(path: Path) -> list[CookieRow]:
    """解析账号 TSV，格式为 username/password/totp/email/email_password/token/cookie。"""
    rows: list[CookieRow] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 7:
            raise ValueError(f"line {line_number}: expected at least 7 tab-separated columns")
        username = parts[-7]
        token = parts[-2]
        cookies = _decode_cookie_blob(parts[-1])
        values = {str(item.get("name")): str(item.get("value", "")) for item in cookies}
        if values.get("auth_token") != token:
            raise ValueError(f"line {line_number}: token column does not match cookie auth_token")
        if not values.get("ct0"):
            raise ValueError(f"line {line_number}: missing ct0 cookie")
        rows.append(CookieRow(line_number=line_number, username=username, token=token, cookies=cookies))
    return rows


def _session_payload(cookies: list[dict[str, object]]) -> dict[str, object]:
    """生成 twitter_cli.auth.load_from_env 能读取的 session JSON。"""
    values = {str(item.get("name")): str(item.get("value", "")) for item in cookies}
    return {
        "auth_token": values["auth_token"],
        "ct0": values["ct0"],
        "cookie_string": "; ".join(
            "%s=%s" % (item.get("name"), item.get("value", "")) for item in cookies
        ),
        "cookies": cookies,
    }


def _parse_status(stdout: str) -> tuple[str, str, bool]:
    """从 status --yaml 输出中提取用户名、ID 和认证状态。"""
    username = ""
    user_id = ""
    authenticated = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == "authenticated: true":
            authenticated = True
        elif stripped.startswith("username:"):
            username = stripped.split(":", 1)[1].strip().strip("'")
        elif stripped.startswith("id:") and not user_id:
            user_id = stripped.split(":", 1)[1].strip().strip("'")
    return username, user_id, authenticated


def check_rows(rows: list[CookieRow], repo: Path, timeout: int) -> list[dict[str, object]]:
    """逐条写入临时 session 文件，并调用 twitter status 做只读验证。"""
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="twitter-cli-cookie-batch-") as tmp:
        tmp_path = Path(tmp)
        for row in rows:
            session_file = tmp_path / f"session-{row.line_number}.json"
            session_file.write_text(json.dumps(_session_payload(row.cookies)), encoding="utf-8")
            os.chmod(session_file, 0o600)

            env = os.environ.copy()
            env["TWITTER_COOKIE_FILE"] = str(session_file)
            env.pop("TWITTER_AUTH_TOKEN", None)
            env.pop("TWITTER_CT0", None)
            proc = subprocess.run(
                ["uv", "run", "twitter", "status", "--yaml"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            reported_username, user_id, authenticated = _parse_status(proc.stdout)
            results.append(
                {
                    "line": row.line_number,
                    "input_username": row.username,
                    "ok": proc.returncode == 0 and authenticated,
                    "reported_username": reported_username,
                    "reported_id": user_id,
                    "cookie_count": len(row.cookies),
                    "error": "" if proc.returncode == 0 else (proc.stderr or proc.stdout)[:180],
                }
            )
    return results


def print_table(results: list[dict[str, object]]) -> None:
    """用 Markdown 表输出脱敏检查结果，方便贴进报告。"""
    print("| line | input | ok | reported | id | cookies | error |")
    print("|---:|---|---|---|---|---:|---|")
    for item in results:
        print(
            "| {line} | {input_username} | {ok} | {reported_username} | {reported_id} | "
            "{cookie_count} | {error} |".format(**item)
        )


def main() -> int:
    """命令入口：读取 TSV 文件，批量验证完整 Cookie 导入链路。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tsv", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    rows = parse_tsv(args.tsv)
    results = check_rows(rows, args.repo, args.timeout)
    if args.json_output:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_table(results)
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
