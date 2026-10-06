#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
push_to_github.py —— 不依赖 git / 不依赖 GitHub 连接器，直接走 REST API 把本地目录推到 GitHub。

为什么需要它：
  WorkBuddy 内置 GitHub 连接器用的是 GitHub App 身份，默认只有公开仓库权限，
  访问私有仓库会 404。用 Personal Access Token 直接调 REST API 可以绕开这个限制。

用法：
  python push_to_github.py --token <PAT>
  python push_to_github.py                      # 从 bridge/.env 或 .env 里读 GITHUB_TOKEN

行为：
  - 递归遍历项目根目录，跳过 .gitignore / 内置排除规则命中的文件（*.bin、.env 等）
  - 与远端 tree 比对 sha，只推有变化的文件（未变化的不产生 commit）
  - 每个文件一个 commit（小仓库够用；大批量会在最后打印汇总）

Token 怎么拿（只需一次）：
  https://github.com/settings/personal-access-tokens  → Fine-grained token
  Resource owner: zuowencheng
  Repository access: Only select repositories → xiaozhi-deskemoji-agent
  Permissions: Contents = Read and write, Metadata = Read-only
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent          # 项目根
TOKEN_FILES = [ROOT / "bridge" / ".env", ROOT / ".env"]

# 内置排除（与 .gitignore 互补，双保险，绝不把密钥和 16MB bin 推上去）
HARD_EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
                     "build", "managed_components", ".workbuddy",
                     # 任务投递箱的运行时状态（含个人任务内容）
                     "inbox", "done", "announced"}
HARD_EXCLUDE_GLOBS = ["*.bin", ".env", "*.token", "mcp_endpoint*.txt", "*.pyc"]


# ---------------------------------------------------------------- utils
def log(msg: str) -> None:
    print(msg, flush=True)


def read_token_from_env_files() -> str | None:
    for f in TOKEN_FILES:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_gitignore_patterns() -> list[str]:
    gi = ROOT / ".gitignore"
    if not gi.exists():
        return []
    out = []
    for line in gi.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s.rstrip("/"))
    return out


def is_excluded(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    if any(p in HARD_EXCLUDE_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    for g in HARD_EXCLUDE_GLOBS:
        if fnmatch.fnmatch(name, g):
            return True
    for p in patterns:
        p = p.rstrip("/")
        if not p:
            continue
        if "/" in p:                                  # 路径型规则：a/b
            if rel == p or rel.startswith(p + "/") or fnmatch.fnmatch(rel, p):
                return True
        else:                                         # 名字型规则：匹配文件名或任一层目录名
            if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) or p in parts[:-1]:
                return True
    return False


def collect_files(patterns: list[str]) -> list[tuple[str, Path]]:
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in HARD_EXCLUDE_DIRS]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(ROOT).as_posix()
            if is_excluded(rel, patterns):
                continue
            files.append((rel, full))
    return sorted(files)


def git_blob_sha(data: bytes) -> str:
    import hashlib
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------- api
class GitHub:
    def __init__(self, token: str, repo: str, branch: str):
        self.token = token
        self.repo = repo
        self.branch = branch

    def _req(self, method: str, path: str, payload: dict | None = None, timeout: int = 60):
        url = f"{API}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "xiaozhi-deskemoji-agent-push")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"{method} {path} -> {e.code} {body[:400]}") from None

    def ping(self) -> dict:
        return self._req("GET", f"/repos/{self.repo}")

    def remote_tree(self) -> dict[str, str]:
        """返回 {path: blob_sha}"""
        out: dict[str, str] = {}
        try:
            info = self._req("GET", f"/repos/{self.repo}/git/ref/heads/{self.branch}")
            sha = info["object"]["sha"]
            tree = self._req("GET", f"/repos/{self.repo}/git/trees/{sha}?recursive=1")
        except RuntimeError as e:
            if "404" in str(e) or "409" in str(e):
                return out          # 空仓库
            raise
        for item in tree.get("tree", []):
            if item.get("type") == "blob":
                out[item["path"]] = item["sha"]
        return out

    def put_file(self, rel: str, content: bytes, message: str):
        b64 = base64.b64encode(content).decode("ascii")
        payload = {"message": message, "content": b64, "branch": self.branch}
        # 已存在的文件必须带 sha 才能更新
        try:
            exist = self._req("GET", f"/repos/{self.repo}/contents/{urllib.parse.quote(rel)}?ref={self.branch}")
            payload["sha"] = exist["sha"]
        except RuntimeError:
            pass
        return self._req("PUT", f"/repos/{self.repo}/contents/{urllib.parse.quote(rel)}", payload)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None)
    ap.add_argument("--repo", default="zuowencheng/xiaozhi-deskemoji-agent")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要推送的文件，不实际推送")
    args = ap.parse_args()

    token = args.token or read_token_from_env_files() or os.environ.get("GITHUB_TOKEN")
    if not token and not args.dry_run:
        log("缺少 token：用 --token <PAT>，或把 GITHUB_TOKEN=xxx 写进 bridge/.env")
        return 2

    patterns = load_gitignore_patterns()
    files = collect_files(patterns)
    log(f"本地待同步文件 {len(files)} 个（已排除密钥与 *.bin）")
    for rel, _ in files:
        log(f"  - {rel}")

    if args.dry_run:
        return 0

    gh = GitHub(token, args.repo, args.branch)
    try:
        info = gh.ping()
        log(f"仓库可达：{info.get('full_name')} private={info.get('private')} "
            f"default_branch={info.get('default_branch')}")
    except RuntimeError as e:
        log(f"无法访问仓库：{e}")
        log("→ 多半是 token 没给这个仓库的 Contents 读写权限，回 Settings 检查一下")
        return 1

    remote = gh.remote_tree()
    log(f"远端已有 {len(remote)} 个文件")

    pushed, skipped, failed = [], [], []
    for rel, full in files:
        data = full.read_bytes()
        if remote.get(rel) == git_blob_sha(data):
            skipped.append(rel)
            continue
        try:
            gh.put_file(rel, data, f"chore: sync {rel}")
            pushed.append(rel)
            log(f"  [OK] {rel} ({len(data)} bytes)")
            time.sleep(0.15)
        except RuntimeError as e:
            failed.append((rel, str(e)))
            log(f"  [FAIL] {rel}: {e}")

    log("\n==== 汇总 ====")
    log(f"推送 {len(pushed)} / 跳过(无变化) {len(skipped)} / 失败 {len(failed)}")
    if failed:
        for rel, err in failed:
            log(f"  FAILED {rel}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
