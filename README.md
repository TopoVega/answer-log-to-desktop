# Agent Workbench · 多 Agent 平台协作工作台

Windows 桌面端工作台。每个 agent 平台各自维护一份「内容产出区」交接文档，由**用户手动**决定哪份文档交给哪个平台阅读 —— 平台之间默认互相不可见，没有共享看板，也没有派活队列。

打包产物是单个 exe，源码仅需 Python 标准库 + pywebview。

## 它解决什么

多个 agent 平台（Qoder / WorkBuddy / Claude Code / Codex / Cursor…）并行工作时，彼此的产出会重复、会互相推翻、会失忆。这个工作台把每一轮「用户问题 → 实际做了什么 → 回答内容」固化成一份**状态文档**（不是流水账：被显式修正过的旧内容会从文档里消失），你可以直接把它拖给另一个 agent 读。

## 接入一个平台（两步）

### 1. 装 skill —— 把地址丢给那个 agent 就行

每个平台的 skill 是仓库里的一个单文件，直接让 agent 自己抓去装：

| 平台 | 安装地址（raw） | 装到 |
|---|---|---|
| WorkBuddy | `https://raw.githubusercontent.com/<owner>/agent-workbench/main/adapters/workbuddy/SKILL.md` | `~/.workbuddy/skills/answer-log-to-workbench/SKILL.md` |
| Qoder | `https://raw.githubusercontent.com/<owner>/agent-workbench/main/adapters/qoder/SKILL.md` | `~/.qoder-cn/skills/agent-workbench/SKILL.md` |
| 其它任何平台 | `https://raw.githubusercontent.com/<owner>/agent-workbench/main/adapters/generic/SKILL.md` | 该工具自己的技能目录，文件名 `agent-workbench-generic/SKILL.md` |

通用适配器不带任何平台身份：`platform` 和 `agent_id` 由那个 agent 在会话开始时自己声明，所以同一平台上可以并存多个 agent 而不互相覆盖。新写一个平台适配器 = 往 `adapters/<平台>/SKILL.md` 放一个文件，接入配置界面会自动列出来。

对那个 agent 说一句：

> 读一下 `https://raw.githubusercontent.com/.../adapters/workbuddy/SKILL.md`，按里面的说明把自己技能目录的 SKILL.md 装好。装完先别执行归档，等我告诉你开始。

工作台界面的「接入配置 → 安装 Skill」里也有同样的地址和一段现成的安装指令可以复制。

### 2. 加 MCP 连接器（可选，但只有这条路能实时绑定身份）

能拉起本地进程的平台（stdio）：

```json
{
  "mcpServers": {
    "agent-workbench": {
      "type": "stdio",
      "command": "D:\\AgentTeamWork\\dist\\AgentWorkbench-mcp\\AgentWorkbench-mcp.exe",
      "args": ["mcp"]
    }
  }
}
```

只接受 URL 的平台（streamable HTTP，**不含任何机器路径**）：

```json
{
  "mcpServers": {
    "agent-workbench": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8734/mcp?platform=<平台>&agent_id=<角色>",
      "headers": { "Authorization": "Bearer <token.json 里的 token>" }
    }
  }
}
```

URL 上的 `platform` / `agent_id` 在 `initialize` 时就绑定身份，所以**不调 `wb_register` 也算接上了** —— 对只会填一个 URL 的平台来说，这是唯一不需要它理解任何约定的接法。端口以 `%LOCALAPPDATA%\AgentWorkbench\port.json` 为准（8734 被占用时会自动退到随机端口）。

只配连接器不装 skill，agent 能调用但不知道该在什么时候调用；只装 skill 不配连接器，它知道该做但调不到工具。**两步都要。**

stdio 要求客户端能在本机拉起进程 —— 网页端 / 云端托管的 agent 做不到，那种只能走 URL 连接器、HTTP 或文件投递，而且三条都只到本机。

## 三条接入通道

按平台能力挑一条，不用全装：

| 通道 | 给谁 | 说明 |
|---|---|---|
| MCP | 任何 MCP 客户端 | 7 个工具；stdio（能拉起本地进程）或 streamable HTTP `/mcp`（只填 URL）。HTTP 下身份按连接隔离，URL 上就能预绑定 |
| HTTP | hook / 脚本 / CLI | `POST /api/turn-log`，localhost + 会话 token；**请求体要写成 UTF-8 文件再用 `--data-binary @turn.json`**，Windows shell 内联中文会按本地代码页发出去，服务端只能报"不是 UTF-8 JSON" |
| 文件投递 | 只会写文件的平台 | 往 `~/agent-workbench/inbox/` 丢 `.md` / `.json`；运行中约 3 秒摄取一次，没运行则在下次启动时摄取 |

三条通道都只到**本机**（`127.0.0.1` 和 `~/agent-workbench`）。纯云端托管的 agent 一条都够不着 —— 那种情况只能把共享目录放进同步盘，或者故意把工作台暴露到公网。

## MCP 工具

| 工具 | 用途 |
|---|---|
| `wb_register` | 会话开始时自报身份，并**绑定后续读取的作用域** |
| `wb_post_log` | 归档一轮（`question` 用用户原话；带 `supersedes` 即为修正） |
| `wb_supersede` | 事后推翻已归档的条目 |
| `wb_get_dossier` / `wb_list_dossiers` | 读**自己平台**的交接文档 |
| `wb_read_board` / `wb_find_agents` | 本平台归档 + 其他平台仅按名字可见 |

跨平台读取一律拒绝。别的平台的内容只能由用户把 `handoff/<平台>.md` 拖过去。

## 从源码运行

```bash
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m workbench.app        # 开桌面窗口
.venv/Scripts/python.exe tests/smoke.py          # 203 项自检
py build.py                                      # 打包出 dist/ 下两个 exe
```

**Windows 上别用 `python`。** 这台机器的 `python` / `python3` 是坏的 WindowsApps 应用别名，任何调用 `exit 49` 且零输出 —— 失败长得和成功一模一样。用 `py`、绝对路径的 `python.exe`，或直接指向打包出的 exe。

## 数据位置

应用数据不随仓库走，换目录、重新打包都不影响已有记录：

| 内容 | 位置 |
|---|---|
| 数据库 | `%LOCALAPPDATA%\AgentWorkbench\workbench.db` |
| 交接文档 | `~/agent-workbench/handoff/<平台>.md` |
| 投递目录 | `~/agent-workbench/inbox/` |
| Markdown 导出 | `~/agent-workbench/logs/`（并镜像到桌面） |

环境变量：`AGENT_WORKBENCH_DESKTOP=0` 关桌面写入，`AGENT_WORKBENCH_ADOPT_DESKTOP=0` 关桌面摄取。

## 两个重置

界面按平台、按 agent 粒度提供，全局按钮已移除：

- **重置产出区** —— 水位线机制：清空交接文档但保留归档历史，且不会下一轮又满回来，可撤销。
- **重置时间线** —— 真删：删归档记录 + 导出目录 md + 桌面上**同名**的 md。只按名精确匹配，绝不清空桌面；非本程序创建的桌面文件会在确认框里单独标出来让你决定。

## 已知边界

- **跨平台隔离是防手滑，不是防恶意。** 对端平台和你共用同一个 Windows 账户，想绕过可以直接读那些文件。真正的决策点是用户手动拖文件。
- **「每轮自动归档」依赖模型自觉。** 工作台提供「有心跳但久未归档」检测器把漏归档变得可见，但检测不等于阻止。真要阻止需要平台侧的回合钩子。
- 平台之间没有任何推送通道，交接全靠人。
- **桌面镜像和桌面摄取默认同时开着**，所以「本程序自己导出到桌面的文件」必须被摄取环节跳过 —— 那些文件带着 entry marker，长得和待摄取的归档一模一样，不跳过就会把每一轮记成两条。跳过依据是 `desktop_mirror.json` 这份「我只写我自己创建的文件」账本。

## License

MIT
