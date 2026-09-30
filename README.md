# CodexSettingFix

**同一台机器上装了 CC Switch、Cockpit 这类账号切换器时，它们会一起改写同一个 Codex 配置目录，把「官方订阅」和「中转线路」写串。这个工具把这件事修好。**

零依赖（只用 Python 标准库），Python 3.11+。

---

## 先看清问题是什么

`$CODEX_HOME`（默认 `~/.codex`）不是某个切换器的私有目录，它是 **Codex CLI 和 Codex 桌面端共用的状态目录**。里面同时住着两类东西：

| 类别 | 内容 | 归谁所有 |
|---|---|---|
| 身份无关 | `[desktop]`、`[plugins.*]`、`[marketplaces.*]`、`[mcp_servers.*]`、`[projects.*]`、`notify`、`model`、以及 `*.sqlite` 会话库 | 桌面端和用户 |
| **身份相关** | `model_provider`、`model_providers.*`、`cli_auth_credentials_store`、`auth.json` 整体 | 各个切换器 |

每个切换器都以为「这个目录是我的」，于是各自往同一个 `config.toml` 里补自己那一块，谁也不知道对方存在。实测到三种真实故障：

**1. 文件被写坏。** 切换器不解析现有 TOML，直接追加自己的块，撞上桌面端已有的键：

```toml
notify = [ "...codex-computer-use.exe", "turn-ended" ]
model_provider = "openai"

notify = [ "...同一个键，重复定义..." ]      # ← TOML 禁止重复键
approval_policy = "never"
```

整个文件变成非法 TOML，Codex 读不进去。现场留下了切换器自己命名的 `config.toml.invalid-toml.<时间戳>` 作为证据。

**2. 线路串设置。** Cockpit 的「API 接管」会把 `base_url` 指向它本机的 sidecar；CC Switch 切换 provider 时会写另一套 `model_providers`。两者共用 `config.toml`，于是就出现「用官方账号启动，却走上中转线路」。

**3. 凭据串设置。** `auth.json` 是整体覆盖的：官方登录是 `{tokens: ...}`，中转是 `{OPENAI_API_KEY: ...}`。谁最后切换谁说了算，另一方的凭据被 `.bak` 掉，而那个 `.bak` 里往往是明文密钥。

### 为什么「每个档案克隆一份 CODEX_HOME」不可行

这是最容易被想到、也最容易踩坑的做法。实测这台机器上的 `~/.codex` 有 **6 个 SQLite 库共约 4 GB**（`logs_2.sqlite` 666 MB、`thread_history_1.sqlite` 177 MB…）。按档案克隆等于把每一份会话历史都复制一遍，既慢又错——而且这些库在应用运行时是被锁住的。

所以真正要换的，只是上表里的**第二类**：一个 `auth.json` 加上 `config.toml` 里那一小组键。这个工具把它叫做**认证面（authentication surface）**：

```
model_provider / model_providers.* / cli_auth_credentials_store
forced_login_method / forced_chatgpt_workspace_id
```

**认证面之外，一个字节都不动。**

---

## 它做什么

```text
~/.settingfix/
  profiles/<名字>/
    auth.json       # 凭据
    surface.toml    # 与之配套的认证面
    meta.json       # 类型、说明、凭据标签
  backups/<id>/     # 每次切换前的完整快照
  active.json       # 当前生效的档案
  shim/             # 可选的 codex 包装脚本
```

### 安装

```bash
git clone https://github.com/Wanttosleep2005/CodexSettingFix.git
cd CodexSettingFix
python -m pip install -e .
```

### 典型流程

```bash
# 1. 先在 Cockpit 里切到官方账号，等它写完，然后存成 official
settingfix capture official --kind official --note "Cockpit 官方订阅"

# 2. 再到 CC Switch 里切到中转，等它写完，存成 relay
settingfix capture relay --kind relay --note "CC Switch 中转"

# 3. 想用官方时
settingfix use official

# 4. 想用中转时
settingfix use relay
```

`capture official` 会做两件净化：丢掉 `auth.json` 里混进来的 `OPENAI_API_KEY`，并把认证面换成「官方 endpoint + 文件凭据」——所以中转线路不可能被带进官方会话。`use` 会先备份当前状态，再整体写入并校验，最后才落盘。

### 命令行接口

| 命令 | 作用 |
|---|---|
| `settingfix doctor` | 取证式体检，见下 |
| `settingfix status` | 当前凭据标签、endpoint、写入者足迹 |
| `settingfix capture <名字> --kind official\|relay` | 存档案 |
| `settingfix use <名字>` | 切换（先备份、后校验） |
| `settingfix diff <名字>` | 档案与当前状态的差异 |
| `settingfix run <名字> -- codex ...` | 切换后直接启动 |
| `settingfix exec -- <程序> ...` | 套用 active 档案后启动（供 shim 用） |
| `settingfix shim install` | 生成 `codex` 包装脚本 |
| `settingfix backup` / `backups` / `restore <id>` | 备份与回滚 |
| `settingfix env` | 启动时会清掉/保留哪些环境变量 |

全局参数 `--home` / `--store`，或环境变量 `CODEX_HOME` / `SETTINGFIX_HOME`。

---

## `doctor`：给证据，不给保证

`doctor` 的每一条结论都带具体的文件名或行号，你可以拿同样的字节自己核对：

```
[ ok ] Codex home
        C:\Users\HP\.codex
[ ok ] config.toml parses
        75 lines
[ ok ] credential
        ChatGPT login · 2745••••
[fail] a previous write produced invalid TOML
        config.toml.invalid-toml.1790650977482634800 -- a switcher appended its block without parsing the file.
[warn] credential copies left in the home
        auth.json.bak -- these can hold a live relay key in clear text.
[warn] Cockpit writes into this home
        6 file(s), e.g. .cockpit_api_takeover.json, .cockpit-provider-model-backup.json ...
[warn] CC Switch writes into this home
        1 file(s), e.g. cc-switch-model-catalog.json
[warn] this home holds live session state
        6 SQLite database(s) totalling 3999 MB. ...
```

检查项：TOML 是否可解析、顶层键是否重复、endpoint 与 `[model_providers.*]` 是否自相矛盾、凭据是否被混写、是否有历史损坏残留、哪些切换器在写这个目录、环境变量是否会盖掉配置、以及这个 home 能不能被克隆。退出码 `0` 干净 / `1` 有警告 / `2` 有错误。

`doctor` 只读，不写任何东西。

---

## 边界，说清楚

- **这不是 CC Switch / Cockpit 的插件。** 这两个软件都没有对外开放的插件接口，所以没有「装进它们里面」这个选项。本工具作用在它们共同的产物——Codex 配置目录——以及（可选）你的 shell 上。这是能达到的最贴近的形态。
- **`use` 是切换时刻生效，不是持续防护。** 你在切换器里手动切号之后，要重新 `settingfix use`。想让每次启动都自动套用当前档案，用 `settingfix shim install` 把包装脚本放进 PATH，之后直接敲 `codex` 就会自动执行 `use` + 清理环境变量。
- 已经在运行的 Codex / 桌面端不会读到新凭据，**必须退出重启**。
- `cli_auth_credentials_store` 只支持 `"file"`。用 `keyring` / `auto` / `ephemeral` 时凭据不在这个目录里，本工具会直接拒绝而不是猜。
- 浏览器 cookie、系统凭据库、企业策略、切换器自己的数据库（`~/.cc-switch/cc-switch.db`、`~/.antigravity_cockpit/`）都不在管辖范围内。

---

## 安全

档案和备份里是**真实凭据**。放在只有你能访问、不同步、不提交的目录里。`.gitignore` 只是辅助，提交前请自己再检查一遍。`status` / `list` / `doctor` 只输出凭据的短标签（账号 ID 打码、密钥只给 sha256 前 8 位），不输出任何 token 原文。

## 开发

```bash
python -m unittest discover -s tests -v
```

78 项测试，覆盖 TOML 手术式改写（含「顶层键被插进表里」这类静默错位回归）、认证面捕获与叠加、凭据标签、档案校验、备份回滚、doctor 各检查项、环境清理与 shim 解析。全部使用虚构凭据和临时目录。CI 在 Windows 与 Ubuntu 上跑 Python 3.11 / 3.12 / 3.13。

## 参考

- [OpenAI Codex 认证文档](https://developers.openai.com/codex/auth)：`cli_auth_credentials_store`、`CODEX_HOME`、登录缓存与刷新。
- [Codex 配置参考](https://github.com/openai/codex/blob/main/docs/config.md)：`model_providers`、`forced_login_method`、配置层优先级。
- [CC Switch](https://github.com/farion1231/cc-switch)：`~/.cc-switch/` 与切换行为。
- [Cockpit Tools](https://github.com/jlcodes99/cockpit-tools)：`~/.codex` 与 `~/.antigravity_cockpit/`。

MIT License.
