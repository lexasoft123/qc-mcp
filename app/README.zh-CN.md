# Patchbay 中文上手指南

Patchbay 是 [qc-mcp](../README.md) 的启动器：它安装 qc-mcp（自带 Python），把服务器
注册到你的 AI 客户端，运行独占设备的守护进程，并在旁边打开 Cortex Control ——
之后你只需要对 Claude（或 Codex、Cursor……）说"给我一个 Plexi 的音色"，它就会在你的
Quad Cortex 上搭出来。

这份指南只讲上手：安装、连接、把 AI 客户端接上，以及界面里每一页是干什么的。
完整的英文说明（设计、打包、守护进程的细节）在 [README.md](README.md)。

> **界面语言**：Patchbay 会跟随系统语言。系统是简体中文，第一次打开就是中文。
> 如果不是，点右上角的齿轮 → 偏好设置 → 第一组"语言"里切换。整个界面立刻切换，
> 包括安装检查、进度提示和更新消息。

## 安装

从 [Releases](https://github.com/lexasoft123/qc-mcp/releases) 下载对应平台的安装包：

- **macOS**：`.dmg`（Apple 芯片和 Intel 各一个）。已用 Developer ID 签名并经过 Apple
  公证，双击即可打开，不会有警告。
- **Windows**：`.exe`。**尚未签名**，SmartScreen 会拦一下：点"更多信息"→"仍要运行"。
  之后自动更新下载的安装包也是如此。

不需要预先安装任何东西，连 Python 都不用：Patchbay 自带 `uv`，第一次运行时它会
下载自己的 Python 3.12 并搭好环境，大约八秒。

## 第一次连接

1. 用 USB 线把 Quad Cortex 接到电脑。
2. 打开 Patchbay，主页会显示一条信号路径：**Claude → Patchbay → Quad Cortex**。
   每一段只有在真正连通时才会亮。
3. 按 **安装并连接**（之后就只是 **连接**）。它会按顺序补齐缺的东西：
   Python 环境、（macOS 上）Cortex Control 的插桩副本、客户端注册、Cortex Control、
   守护进程。第一次要几分钟，主要花在构建插桩副本上；之后只要几秒。
4. 看到"已连接 —— Claude 可以访问你的 Quad Cortex 了"就好了。

全程 **不会向你要密码**。想看每一步做了什么，去"安装"页面。

### macOS 和 Windows 的区别

- **macOS**：IOKit 只把设备交给一个程序，所以 Patchbay 会复制一份 Cortex Control
  到本地、重新签名并注入一个小的拦截层（这就是"插桩副本"），让守护进程搭上
  Cortex Control 自己的会话。**你已安装的 Cortex Control 不会被动过。**
- **Windows**：不需要复制任何东西。守护进程在 Cortex Control 旁边打开第二个
  非独占的 HID 句柄，两边同时读写同一台设备。

两种情况下你都可以一边用 Cortex Control，一边让 AI 改预设。

## 把 AI 客户端接上

第一次运行安装时，Patchbay 会把 `quad-cortex` 注册到这台电脑上 **已安装** 的所有
客户端。之后在 **控制台 → 管理客户端…** 里增减。每个客户端写入的都是同一个条目：
`qc-mcp` 可执行文件、`--attach`、守护进程的套接字路径 —— 写进各自的配置文件，
文件的其余内容保持原样。按下 **应用** 后，对话框会告诉你每个客户端接下来怎么生效。

| 客户端 | 配置文件 | 写入方式 | 然后 |
|---|---|---|---|
| Claude Code | `~/.claude.json` | 有 `claude` 命令时用 `claude mcp add --scope user`，否则合并 JSON | 新会话立即可用；`claude mcp list` 可确认 |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json`（Windows：`%APPDATA%\Claude\…`） | 合并到 `mcpServers` | 退出并重新打开 —— 它启动时读取该文件 |
| Cursor | `~/.cursor/mcp.json` | 合并到 `mcpServers` | 设置 → MCP 里会列出；关着就打开 |
| VS Code | `~/Library/Application Support/Code/User/mcp.json`（Windows：`%APPDATA%\Code\User\…`） | 合并到 `servers` | 命令面板 **MCP: List Servers**，启动它 |
| Zed | `~/.config/zed/settings.json`（Windows：`%APPDATA%\Zed\…`） | 合并到 `context_servers` | 重启 Zed；Agent 面板的设置里会列出 |
| **Codex** | `~/.codex/config.toml` | 单独一张 `[mcp_servers.quad-cortex]` 表，其余不动 | 新会话立即可用；`codex mcp list` 可确认 |

### Codex 用户请注意

Codex 的配置是 **TOML** 而不是 JSON，之前的版本没有把它列进来，所以"注册"这一步
对 Codex 用户来说是不完整的 —— 这就是那种"有点摸不着头脑"的感觉的来源。现在
Patchbay 会直接写 `~/.codex/config.toml`，并且只碰属于自己的那一张表。

如果你想手动检查或手动写，条目长这样（把路径换成 **管理客户端…** 对话框里显示的
真实路径）：

```toml
[mcp_servers.quad-cortex]
command = "/绝对路径/qc-mcp/.venv/bin/qc-mcp"
args = ["--attach", "--socket", "/绝对路径/daemon.sock"]
```

Windows 上路径里的反斜杠要写两个：`"C:\\Users\\你\\…\\qc-mcp.exe"`。

### 其他客户端

不在列表里的客户端，手动加同一个条目即可（JSON）：

```json
{ "mcpServers": { "quad-cortex": {
    "command": "/绝对路径/qc-mcp/.venv/bin/qc-mcp",
    "args": ["--attach", "--socket", "/绝对路径/daemon.sock"] } } }
```

真实路径在 **管理客户端…** 对话框底部，已经替你填好。仓库位置在 偏好设置 → 位置；
套接字在 macOS 的 `~/Library/Application Support/qc-mcp/`、Windows 的
`%LOCALAPPDATA%\qc-mcp\` 下。

`--attach` 很重要：它让多个客户端共用一台设备。没有它的条目（旧版 `install.sh`
写的那种）会自己去打开设备，在守护进程占用设备时就会失败 —— 控制台里琥珀色的
"自行打开设备"就是这个意思，点 **重新指向** 即可改写。

## 界面里的每一页

- **主页** —— 信号路径和一个大按钮。按钮的文字随状态变：连接、安装并连接、
  打开 Cortex Control、断开。
- **控制台** —— 三个独立模块：MCP 注册（哪些客户端有条目）、qc-mcp 守护进程
  （模式、pid、套接字、实测的每秒报告数）、Cortex Control（macOS 上还有插桩副本的
  签名状态和重新构建按钮）。模式选择器可以在运行中的守护进程上切换 自动 / 桥接 / 直连。
- **电平平衡** —— 把几个预设放到一个工作台上，Patchbay 逐个加载到设备，用方向键
  A/B 对比，用旋钮调整 **通道输出** 的电平（这个值保存在预设里，所以保存后是永久的）。
  `⌘S` 保存；"自动保存"默认关闭，因为它会直接改真实的预设文件。
- **安装** —— 七项检查（Windows 五项），每一项都是实际测量而不是存下来的标记，
  能修的旁边有"修复"。
- **日志** —— 拦截层写下的帧日志：方向、字节数、十六进制，可以只看错误。

### 三种连接模式

- **自动**（默认）：Cortex Control 开着就共享它的会话，没开就直接接管设备。
- **桥接**：只走 Cortex Control 的会话。macOS 上需要插桩副本运行。
- **直连**：守护进程独占设备。macOS 上必须先退出 Cortex Control。

## 更新

Patchbay 每六小时（以及启动三秒后）问一次 GitHub 有没有新版本。有的话底栏右下角会
出现一个按钮。Windows 会在后台下载并在你下次退出时安装（或者按"重启以更新"）；
macOS 会打开发布页面，由你自己安装 dmg。无论哪个平台，没有你按下按钮什么都不会装。
偏好设置里可以关掉自动检查，也可以"立即检查"。

## 常见问题

- **"USB 上没有找到 Quad Cortex"** —— 检查线缆；换一个 USB 口；确认设备已开机。
  安装页面的最后一项会告诉你它在等哪一个 USB ID。
- **macOS 上"等待 Cortex Control —— 桥接模式需要插桩副本运行"** —— 桥接模式需要插桩
  副本打开着。按主页的"打开 Cortex Control"，或者把模式切成 自动 / 直连。
- **Cortex Control 更新后桥接不工作了** —— 插桩副本和源应用版本不一致，主页和控制台
  都会提示；点"重新构建"。偏好设置里可以让它自动重建。
- **Claude 里看不到 quad-cortex** —— 控制台 → MCP 注册 看那个客户端是不是"已安装"；
  是"自行打开设备"就点"重新指向"；不在列表里就在"管理客户端…"里勾上。
- **想提交问题** —— 偏好设置里打开"写入帧日志"，复现一次，把日志一起附上。

## 反馈

翻译和引导流程都欢迎指正：在 [GitHub Issues](https://github.com/lexasoft123/qc-mcp/issues)
开一个 issue，中文英文都可以。
