# answer-log-to-desktop

一个 WorkBuddy / Claude 风格的 **skill**：让 Agent 在每次回答完成后，自动把本轮「做了什么 + 回答了什么」写成一份 Markdown 存档到**桌面**。

文件命名直接用**用户原始问题**，不加日期、不加前缀、不加序号。

## 效果

问一句「帮我写一个 skill」，桌面上就会出现：

```
C:\Users\<你>\Desktop\帮我写一个skill.md
```

内容形如：

```markdown
# 帮我写一个skill

- **时间**: 2026-10-10 21:16 (本地时区)
- **状态**: 已完成

## 一、本次做了什么

- 新建了 `SKILL.md` —— 定义触发条件与归档流程
- 新建了 `scripts/save_answer_log.py` —— 负责定位桌面并写入文件
- 关键决策：文件名默认不修改，仅在系统拒绝时替换非法字符

## 二、回答内容

<本轮给用户的最终回复原文>
```

## 安装

把本仓库克隆（或复制）到你的 skills 目录：

```bash
# 用户级（所有项目可用）
git clone https://github.com/<你的用户名>/answer-log-to-desktop.git \
  ~/.workbuddy/skills/answer-log-to-desktop
```

Windows 对应路径为 `C:\Users\<你>\.workbuddy\skills\answer-log-to-desktop`。

也可以直接下载 Release 里的 `answer-log-to-desktop.zip`，解压到同一目录。

## 使用

在对话里说一句触发语即可加载，之后**本会话内每一轮回答**都会自动归档：

- 「把回答归档到桌面」
- 「每次回答后生成一个 md 到桌面」
- 「回答归档」
- 「把这次做了什么记下来」

想停下就说「停止归档」或「不用记了」。

## 目录结构

```
answer-log-to-desktop/
├── SKILL.md                      # 技能定义：触发条件、归档流程、命名规则
└── scripts/
    └── save_answer_log.py        # 归档脚本：定位桌面 + 生成文件名 + 写入
```

## 脚本用法

```bash
python scripts/save_answer_log.py \
  --question "用户原始问题" \
  --body-file /path/to/body.md
```

| 参数 | 说明 |
|------|------|
| `--question` | **必填**。用户原始问题，直接作为文件名 |
| `--body-file` | Markdown 正文文件路径（长文本推荐） |
| `--body` | 内联 Markdown 正文（短文本） |
| `--mode` | `overwrite`（默认）/ `version`（同名加 `(2)`）/ `append` |
| `--desktop` | 手动指定输出目录 |
| `--print-desktop` | 只打印探测到的桌面路径 |
| `--dry-run` | 只计算路径，不写文件 |

输出为单行 JSON：

```json
{"ok": true, "path": "C:\\Users\\topol\\Desktop\\帮我写一个skill.md",
 "filename": "帮我写一个skill.md", "name_modified": false, "bytes": 812}
```

## 命名规则（重要）

默认**原生不修改**——文件名就是问题原文。只有在操作系统会拒绝该文件名时才做最小替换：

| 情况 | 处理 |
|------|------|
| 含 `\ / : * ? " < > \|` | 替换为全角等价字符（`／ ： ？ ＊ ＂ ＜ ＞ ｜`），视觉几乎无差别 |
| 含换行 | 折叠为单个空格 |
| 控制字符 | 丢弃 |
| 以 `.` 或空格结尾 | 剔除 |
| 命中 `CON` `PRN` `AUX` `NUL` `COM1-9` `LPT1-9` | 末尾加 `_` |
| 超过 120 字符 | 截断（完整问题仍保留在文件内的 `# 标题`） |

返回值里的 `name_modified` 标明是否触发了兜底。

## 跨平台

桌面路径探测：

- **Windows**：`SHGetFolderPathW(CSIDL_DESKTOPDIRECTORY)`，兼容 OneDrive 重定向的桌面
- **macOS**：`~/Desktop`
- **Linux**：`xdg-user-dir DESKTOP`，回退到 `~/Desktop`

若桌面目录不存在，回退到用户主目录，并在 JSON 的 `desktop` 字段中如实返回。

## License

MIT
