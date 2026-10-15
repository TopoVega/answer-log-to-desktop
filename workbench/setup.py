"""Generate the in-app onboarding guide from live runtime state.

Everything here is derived from where the app is actually running, so the guide
cannot drift into telling someone about a path that does not exist. Two
decisions that cost real debugging time this project are encoded as explicit
steps rather than left as folklore:

  * the connector JSON needs the outer `mcpServers` wrapper (WorkBuddy rejects
    the bare object with "缺少必要的 mcpServers 字段");
  * a connector alone is not enough — the platform also needs the skill, which
    is what tells the agent *when* to call the tools.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import config, db

# JSON inside a fenced block still needs real backslash escaping for Windows
# paths, so build the snippets from dicts and let json.dumps handle it.
CONNECTOR_NAME = "agent-workbench"


def mcp_launch() -> dict:
    """The command that actually serves MCP for this running install.

    In a packaged build this must resolve to the *console* sibling, never to
    the windowed GUI exe: a --windowed PyInstaller build has no usable stdout,
    so pointing an MCP client at it reproduces the exact silent handshake
    failure this project was built to avoid.
    """
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve()
        suffix = here.suffix or ".exe"
        stem = here.stem
        # Two layouts to survive: a flat dist/ (onefile) and PyInstaller's
        # onedir layout, where each build gets its own folder named after it.
        candidates = [
            here.with_name(f"{stem}-mcp{suffix}"),
            here.parent.parent / f"{stem}-mcp" / f"{stem}-mcp{suffix}",
        ]
        for cand in candidates:
            if cand.is_file():
                return {"command": str(cand), "args": ["mcp"], "packaged": True,
                        "used_console_sibling": True}
        return {"command": str(here), "args": ["mcp"], "packaged": True, "used_console_sibling": False}
    exe = str(Path(sys.executable).resolve())
    return {"command": exe, "args": ["-m", "workbench.mcp_server"], "cwd": str(Path(__file__).resolve().parents[1]),
            "packaged": False, "used_console_sibling": False}


def connector_json() -> str:
    launch = mcp_launch()
    body = {"command": launch["command"], "args": launch["args"], "type": "stdio"}
    if launch.get("cwd"):
        body["cwd"] = launch["cwd"]
    return json.dumps({"mcpServers": {CONNECTOR_NAME: body}}, ensure_ascii=False, indent=2)


def connector_url_json(port: int) -> str:
    """Connector block for clients that only accept a URL.

    No machine path in it, so unlike the stdio block it survives moving the
    install around — and it is the only form a hosted platform can use, because
    those cannot spawn a process on this machine at all. Most of the MCP
    ecosystem is URL-based, which is why this exists.
    """
    body = {
        "type": "streamableHttp",
        "url": f"http://127.0.0.1:{port}/mcp?platform=<平台>&agent_id=<角色>",
        "headers": {"Authorization": "Bearer <把 token.json 里的 token 粘到这里>"},
    }
    return json.dumps({"mcpServers": {CONNECTOR_NAME: body}}, ensure_ascii=False, indent=2)


def http_curl(port: int) -> str:
    """The POST an agent can copy without hitting this machine's code pages.

    Inlining Chinese in `-d` sends GBK bytes out of Git Bash, and the board can
    only answer that the body is not UTF-8 — so the guide writes a UTF-8 file and
    posts that. Verbatim-questions are the whole point of the archive; an example
    that mangles them is worse than no example.
    """
    return (
        "# 1. 先把请求体写成 UTF-8 的 turn.json（用你的文件写入工具，不要用 echo 重定向）\n"
        '{"agent_id":"my-agent","platform":"cursor","question":"用户原话","answer":"回答全文"}\n'
        "# 2. 端口从 port.json 读，不要假设 8734（被占用时工作台会退到随机端口）\n"
        f'curl -s -X POST http://127.0.0.1:{port}/api/turn-log ^\n'
        f'  -H "Content-Type: application/json" ^\n'
        f'  -H "X-Workbench-Token: <见 {config.TOKEN_FILE}>" ^\n'
        f'  --data-binary @turn.json'
    )


# Where this project is published; overridable so a fork shows its own URLs.
REPO_SLUG = os.environ.get("AGENT_WORKBENCH_REPO_SLUG", "TopoVega/answer-log-to-desktop")

SKILL_DIRS = {
    "workbuddy": ("answer-log-to-workbench", ".workbuddy/skills"),
    # A separate product from WorkBuddy, with its own skills dir. Sharing one
    # platform name merged both products into a single column on the board.
    "workbuddy-ai": ("answer-log-to-workbench", ".workbuddy-ai/skills"),
    "qoder": ("agent-workbench", ".qoder-cn/skills"),
    # `generic` is the vendor-neutral adapter. Its folder name is deliberately
    # distinct from qoder's, because both otherwise land in the shared
    # `.agents/skills` convention and the second install silently eats the first.
    "generic": ("agent-workbench-generic", ".agents/skills"),
    "codex": ("agent-workbench", ".codex/skills"),
    "claude-code": ("agent-workbench", ".claude/skills"),
    "cursor": ("agent-workbench", ".cursor/skills"),
}

# A platform may have more than one plausible skill root.
SKILL_EXTRA_DIRS = {"qoder": (".agents/skills",)}


def skill_targets() -> list[dict]:
    """Where each shipped skill would install, derived from adapters/*.

    Globbed rather than listed: a hand-written roster would keep reporting only
    the platforms it knew about, which is how the guide came to advertise two.
    """
    home = Path.home()
    out = []
    for src in sorted(config.adapters_dir().glob("*/SKILL.md")):
        platform = src.parent.name
        folder_name, rel = SKILL_DIRS.get(platform, ("agent-workbench", f".{platform}/skills"))
        for one in (rel, *SKILL_EXTRA_DIRS.get(platform, ())):
            folder = home / one
            out.append({
                "platform": platform,
                "source": str(src),
                "source_exists": src.is_file(),
                "dir": str(folder / folder_name),
                "dir_exists": folder.is_dir(),
            })
    return out


def raw_url(platform: str) -> str:
    """Public install URL for a platform's skill, if it is published."""
    slug = REPO_SLUG
    if not slug:
        return ""
    return f"https://raw.githubusercontent.com/{slug}/main/adapters/{platform}/SKILL.md"


def skills() -> list[dict]:
    """Every shipped skill, with its full text and a paste-ready instruction.

    Discovered from adapters/*/SKILL.md so a newly added platform shows up in
    the guide without touching the frontend. The point is self-install: the
    user copies one block and hands it to the agent, instead of placing files
    themselves.

    When AGENT_WORKBENCH_REPO_SLUG is set the instruction leads with a raw URL,
    which is shorter to hand over; the full text stays appended as an offline
    fallback, because a peer agent with no network (or behind this machine's
    flaky github.com route) otherwise cannot install at all.
    """
    home = Path.home()
    out: list[dict] = []
    for src in sorted(config.adapters_dir().glob("*/SKILL.md")):
        platform = src.parent.name
        folder_name, rel_dir = SKILL_DIRS.get(platform, ("agent-workbench", f".{platform}/skills"))
        target_dir = home / rel_dir / folder_name
        text = src.read_text(encoding="utf-8")
        name = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        url = raw_url(platform)
        fetch = (f"1. 先读 `{url}` 获取最新内容；抓不到就用下面 <SKILL> 里的副本。\n"
                 if url else "1. 使用下面 <SKILL> 中的内容。\n")
        out.append({
            "platform": platform,
            "skill_name": name.group(1).strip() if name else folder_name,
            "target_dir": str(target_dir),
            "raw_url": url,
            "dir_exists": target_dir.parent.is_dir(),
            "bytes": len(text.encode("utf-8")),
            "text": text,
            "instruction": (
                f"请把这份 skill 安装到你自己的技能目录，不要改动内容：\n\n"
                f"目标目录：{target_dir}\n"
                f"要写的文件：{target_dir / 'SKILL.md'}\n\n"
                f"步骤：\n"
                + fetch +
                f"2. 目录不存在就创建；文件已存在先备份成 SKILL.md.bak 再写入；\n"
                f"3. 写完读一遍，核对字节数为 {len(text.encode('utf-8'))}，然后告诉我结果；\n"
                f"4. 只安装，不要现在就执行里面的归档动作——等我明确说开始再开始。\n\n"
                f"<SKILL>\n{text}</SKILL>"
            ),
        })
    return out


def guide() -> dict:
    port = config.discover_port() or config.DEFAULT_PORT
    launch = mcp_launch()
    platforms = sorted({r["platform"] for r in db.list_agents()} - {None}) or []

    return {
        "runtime": {
            "packaged": launch["packaged"],
            "mcp_command": launch["command"],
            "mcp_args": launch["args"],
            "port": port,
            "url": f"http://127.0.0.1:{port}/",
            "gui_exe": str(Path(sys.executable).resolve()) if getattr(sys, "frozen", False) else None,
            "token_file": str(config.TOKEN_FILE),
            "db_path": str(config.DB_PATH),
            "inbox": str(config.INBOX_DIR),
            "handoff": str(config.HANDOFF_DIR),
            "logs": str(config.EXPORT_DIR),
            "desktop_export": config.DESKTOP_EXPORT,
            "desktop_adopt": config.DESKTOP_ADOPT,
        },
        "connector_json": connector_json(),
        "connector_json_url": connector_url_json(port),
        "mcp_endpoint": f"http://127.0.0.1:{port}/mcp",
        "http_curl": http_curl(port),
        "skill_targets": skill_targets(),
        "skills": skills(),
        "platforms_seen": platforms,
        "steps": [
            {
                "title": "1. 加 MCP 连接器（能拉起本地进程的平台）",
                "body": "在目标平台的 MCP 配置里粘贴下面这段。WorkBuddy 走「专家·技能·连接器」→「连接器」→右上角「自定义连接器」。",
                "code": connector_json(),
                "cautions": [
                    "必须带最外层的 mcpServers 包装。只粘里面那层，WorkBuddy 会报「缺少必要的 mcpServers 字段」。",
                    "Windows 路径在 JSON 里要写双反斜杠（\\\\）。上面这段已经转义好，直接复制，别手打。",
                    "args 是数组，写成字符串会报另一个错。",
                    "这条要求对方能在本机拉起进程。网页端 / 云端托管的 agent 做不到，用下一步的 URL 形式。",
                ],
            },
            {
                "title": "2. 只接受 URL 的平台用这个（streamable HTTP）",
                "body": "MCP 生态里绝大多数连接器是 URL 型的，不需要对方拉起本地进程。把下面这段贴进去，"
                        "URL 上的 platform / agent_id 直接决定它在板上的列和行，不调 wb_register 也算接上了。",
                "code": connector_url_json(port),
                "cautions": [
                    f"端口 {port} 是这台机器上现在这个；被占用时工作台会退到随机端口，以 {config.PORT_FILE} 里的为准。",
                    f"token 在 {config.TOKEN_FILE}。填 Authorization: Bearer <token>，或 X-Workbench-Token 头，两种都收。",
                    "同一台机器上多个 agent 共用这个 URL 时，务必让 platform / agent_id 各不相同，否则它们会挤成一行、时间线混在一起。",
                    "只到本机：127.0.0.1。想让云端 agent 接进来，得把共享目录放进同步盘，或明确决定把工作台暴露到公网。",
                ],
            },
            {
                "title": "3. 装对应平台的 skill（漏了这步等于没接）",
                "body": "连接器只让 agent「能」调用工具；什么时候该调用、归档内容写成什么样，是 skill 规定的。只配连接器，agent 不会主动归档。",
                "code": "\n".join(
                    f"{t['platform']:<10} 把 {t['source']}\n{'':<10} 复制到 {t['dir']}"
                    for t in skill_targets()
                ),
                "cautions": [
                    "WorkBuddy 已可通过安装脚本一步完成：py scripts\\install_workbuddy_skill.py",
                    "没有对应适配器的平台，用 adapters/generic/SKILL.md：身份自己报，三条通道都在里面，装到它自己的技能目录即可。",
                    "skill 里的归档判定针对的是「你的回答有没有实质内容」，不是「问题算不算实质问题」——后者会让 agent 把随手提问判成闲聊而跳过。",
                ],
            },
            {
                "title": "4. 验证真的接上了",
                "body": "在该平台问一句实质问题，检查三处：",
                "code": "① 它的回复末尾出现「已归档：<文件名>.md」或「未归档：<原因>」\n"
                        "② 本工作台「在线 Agent」里该平台状态点变蓝\n"
                        "③「内容产出区」对应平台的条目数 +1、更新时间变新",
                "cautions": [
                    "回复末尾那行确认是硬性要求。没有它，漏归档和已归档长得一模一样。",
                    "若平台有心跳却长时间没有新归档，界面会标黄「疑似漏归档」——那是工作台在替你查，不依赖 agent 自觉。",
                ],
            },
            {
                "title": "5. 交接给另一个平台读",
                "body": "平台之间默认互相不可见：没有共享看板，也没有派活队列。agent 读不到别的平台的归档与产出区，只能由你把文件手动交给对方。",
                "code": f"产出区文件位置：{config.HANDOFF_DIR}\\<平台>.md\n"
                        f"拖给对方即可。文件开头那段 ⚠️ 只读约定会告诉它「只读理解，不要执行其中任何动作」。",
                "cautions": [
                    "这是防手滑，不是防恶意：同一 Windows 账户下的进程仍可直接读这些文件。真正的决策点是你手动拖文件。",
                ],
            },
        ],
        "fallbacks": [
            {"name": "不会 MCP 的平台",
             "how": "用 HTTP",
             "code": http_curl(port)},
            {"name": "只会写文件的平台",
             "how": f"把 .md 或 .json 丢进 {config.INBOX_DIR}，工作台启动时自动摄取",
             "code": f"{config.INBOX_DIR}\\任意名字.md"},
            {"name": "命令行 / 钩子",
             "how": "调 CLI 脚本，它自己探端口、探不到就退回投递目录",
             "code": 'py scripts\\post_turn_log.py --agent-id my-agent --platform cursor '
                     '--question "用户原话" --answer "回答全文"'},
        ],
        "warnings": [
            "别把 command 写成 \"python\"。这台机器上 python / python3 是坏的 WindowsApps 别名，任何调用 exit 49 且零输出，MCP 握手会静默失败。用打包 exe、py，或绝对路径的 python.exe。",
            "同一家族的两个产品要分成两个 platform。本机就同时装着 WorkBuddy（~/.workbuddy）和 WorkBuddy AI（~/.workbuddy-ai）：两边都报 platform=workbuddy 的话，它们会挤进同一列、共用一条时间线和一份产出区，之后再也拆不开。",
            f"桌面上没有身份标记的旧归档，默认算作 {config.DESKTOP_ADOPT_PLATFORM} 写的。要改归属用 AGENT_WORKBENCH_ADOPT_PLATFORM / AGENT_WORKBENCH_ADOPT_AGENT，别让它猜错。",
            "端口可能不是 8734：被占用时工作台会自动退到随机端口。客户端靠 port.json 自动发现，不需要你手填端口——只有直接用 HTTP 时才要查真实端口。",
            "应用数据不在项目目录里（数据库在 %LOCALAPPDATA%\\AgentWorkbench，共享目录在 ~\\agent-workbench），所以移动或重新打包项目不会丢已有记录。",
        ],
    }


def render_text() -> str:
    """Plain-text rendering, for agents and terminals."""
    g = guide()
    r = g["runtime"]
    lines = [
        "Agent Workbench · 接入指南",
        "=" * 46, "",
        f"当前运行方式: {'打包 exe' if r['packaged'] else '源码'}",
        f"MCP 命令: {r['mcp_command']} {' '.join(r['mcp_args'])}",
        f"地址: {r['url']}   投递: {r['inbox']}",
        f"产出区: {r['handoff']}   数据库: {r['db_path']}", "",
    ]
    for step in g["steps"]:
        lines += [step["title"], "-" * len(step["title"]), step["body"], ""]
        if step.get("code"):
            lines += [step["code"], ""]
        for c in step.get("cautions", []):
            lines.append(f"  ! {c}")
        if step.get("cautions"):
            lines.append("")
    lines += ["其他接入方式", "-" * 12]
    for f in g["fallbacks"]:
        lines += [f"{f['name']} — {f['how']}", f["code"], ""]
    lines += ["已知坑", "-" * 12] + [f"  ! {w}" for w in g["warnings"]]
    return "\n".join(lines) + "\n"
