"""Push the current HEAD to GitHub through the Git Data API.

Used because git smart-HTTP to github.com is unreliable on this network while
api.github.com is fine. The commit is created with the *remote* tip as its
parent, so existing history is preserved as ancestors rather than force-pushed
away.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

OWNER, REPO, BRANCH = "TopoVega", "answer-log-to-desktop", "main"
API = "https://api.github.com"
TOKEN = os.environ.get("GH_PUSH_TOKEN") or ""

# Superseded by adapters/workbuddy/*; leaving a root SKILL.md in a repo whose
# install instructions point elsewhere is how agents end up installing the wrong one.
DELETE = ["SKILL.md", "scripts/save_answer_log.py"]


def api(method: str, path: str, body=None, tries: int = 4):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:300]
            except Exception:
                pass
            if exc.code in (404, 409, 422) and attempt < tries - 1:
                time.sleep(2)
                continue
            print(f"    HTTP {exc.code} {method} {path}\n      {detail}")
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == tries - 1:
                raise
            print(f"    retry {attempt + 1} ({type(exc).__name__}) {method} {path}")
            time.sleep(2 + attempt * 2)
    raise RuntimeError("unreachable")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout.strip()


def main() -> int:
    if not TOKEN:
        print("缺少 GH_PUSH_TOKEN 环境变量")
        return 2

    head = git("rev-parse", "HEAD")
    print(f"本地 HEAD: {head[:8]}")

    st, ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    base_sha = ref["object"]["sha"]
    print(f"远端 tip : {base_sha[:8]}")

    # Entries are "mode sha path" from ls-tree; paths with spaces come quoted.
    entries = []
    for line in git("ls-tree", "-r", head).splitlines():
        meta, rest = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob":
            continue
        path = rest.split("\t", 1)[-1] if "\t" in rest else rest
        entries.append((mode, sha, path.strip('"')))
    print(f"待上传 blob: {len(entries)} 个")

    tree_items = []
    for i, (mode, _sha, path) in enumerate(entries, 1):
        # Read exact bytes from the commit, not through cat-file's text pipeline.
        blob_src = subprocess.run(["git", "show", f"{head}:{path}"], capture_output=True, check=True).stdout
        st, made = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs", {
            "content": base64.b64encode(blob_src).decode(), "encoding": "base64"})
        tree_items.append({"path": path, "mode": mode, "type": "blob", "sha": made["sha"]})
        if i % 10 == 0 or i == len(entries):
            print(f"  blobs {i}/{len(entries)}")

    # GitHub rejects sha:null for a path the base tree does not contain, so
    # deletions are only emitted for files that are actually still there.
    st, basetree = api("GET", f"/repos/{OWNER}/{REPO}/git/trees/{base_sha}?recursive=1")
    remote_paths = {t["path"] for t in basetree.get("tree", []) if t["type"] == "blob"}
    to_delete = [p for p in DELETE if p in remote_paths]
    skipped = [p for p in DELETE if p not in remote_paths]
    for path in to_delete:
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
    print(f"树条目: {len(tree_items)}（删除 {len(to_delete)} 个"
          + (f"，已不存在故跳过 {len(skipped)} 个" if skipped else "") + "）")

    st, tree = api("POST", f"/repos/{OWNER}/{REPO}/git/trees", {"base_tree": base_sha, "tree": tree_items})
    print(f"新 tree  : {tree['sha'][:8]}")

    message = ("feat: per-platform handoff workbench as a Windows desktop app\n\n"
               "Replaces the single-file answer-log skill. Each agent platform keeps one\n"
               "living handoff document that the user drags to another platform by hand;\n"
               "cross-platform reads are refused at the MCP layer rather than filtered.\n\n"
               "Removes the superseded root SKILL.md and scripts/save_answer_log.py -\n"
               "the WorkBuddy skill now lives at adapters/workbuddy/SKILL.md.\n\n"
               "203 self-checks in tests/smoke.py; package with py build.py.")
    st, commit = api("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                     {"message": message, "tree": tree["sha"], "parents": [base_sha]})
    print(f"新 commit: {commit['sha'][:8]}  parent={base_sha[:8]}")

    st, upd = api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
                  {"sha": commit["sha"], "force": False})
    print(f"ref 更新 : HTTP {st} -> {upd.get('object', {}).get('sha', '')[:8]}")
    print(f"\nhttps://github.com/{OWNER}/{REPO}/tree/{BRANCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
