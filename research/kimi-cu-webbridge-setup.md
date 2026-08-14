# Kimi Code CLI + Computer Use + WebBridge 安装笔记（2026-08-14）

用户要求安装 Kimi 官方 webbridge 与 computer-use 并用于干活。全部完成，验证通过。

## 已安装组件

- **Kimi Code CLI**：npm i -g @moonshot-ai/kimi-code（v0.36.0），二进制 ~/.local/bin/kimi
  - 非交互：kimi -p "<prompt>"；登录：kimi login（设备码流程）
  - 配置目录：~/.kimi-code/（config.toml、plugins/）
- **Kimi Computer Use（kimi-cu v0.5.8）**：
  - 插件：~/.kimi-code/plugins/kimi-cu（zip: cdn.kimi.com/kimi-computer-use/latest/kimi-cu-plugin.zip）
  - App：/Applications/KimiCU.app（zip: .../latest/KimiCU.app.zip，用 ditto 解压保签名；unzip+cp 会坏签名）
  - 服务：smAppService ai.kimi.cu.service；权限由 launchd 服务持有（xpc-ping: accessibility=true screenRecording=true）
  - MCP：/Applications/KimiCU.app/Contents/MacOS/kimi-cu mcp（stdio）——工具：list_apps / get_app_state(ax+截图) /
    click(index 或截图坐标) / type_text / press_key / scroll / set_value / perform_secondary_action / select_text / drag
  - 关键：kimi-cu 的 AX 树能读到 Electron/Chrome 网页内容（Hermes、Chrome 页面元素全部可见），
    后台点击不抢鼠标不切前台。click 用 index 需同会话先 get_app_state 缓存快照，每次操作后重取快照。
  - 排障：kimi-cu service-status / xpc-ping / install / request-permissions --ax --screen
- **Kimi WebBridge（v1.11.5）**：
  - 插件：~/.kimi-code/plugins/kimi-webbridge（zip: code.kimi.com/kimi-code/plugins/official/kimi-webbridge.zip）
  - 守护进程：~/.kimi-webbridge/bin/kimi-webbridge-darwin-arm64（裸二进制，无 .zip 后缀；
    URL: cdn.kimi.com/webbridge/latest/releases/<binaryAssetName>），命令 start/status/restart/stop/logs
  - 端口 127.0.0.1:10086，API：POST /command {"action":..., "args":..., "session":"..."}
    （navigate/find_tab/snapshot/click/fill/evaluate/cdp/screenshot/network/list_tabs/close_tab 等）
  - Chrome 扩展：~/.kimi/kimi-webbridge-extension（zip: kimi-web-img.moonshot.cn/webbridge/latest/extension/kimi-webbridge-extension.zip）
    extension_id=hinhmbbmelmmgiehkfmmkmfndadahmkk，已加载并连接（status: extension_connected=true）
  - 扩展安装自动化经验（kimi-cu 驱动 chrome://extensions）：
    1) 开发者模式是 AXCheckBox；2) 按钮叫「加载未打包的扩展程序」；3) 文件对话框是 Chrome 窗口的 AXSheet；
    4) Cmd+Shift+G 前往文件夹后，路径字段是【追加】不是替换——必须用 set_value 原子替换再回车；
    5) 导航成功后「选择」才从 disabled 变可点。
- **插件注册**：~/.kimi-code/plugins/installed.json
  {version:1, plugins:[{id, root(绝对路径), source:"official", enabled, installedAt, updatedAt, originalSource, capabilities, github}]}
  注：hermes plugins enable 只认原生插件，dashboard-only 插件需手动写 config.yaml 的 plugins.enabled——与本项目无关，记此备忘。

## 待办

- kimi login 设备码授权（用户浏览器页面完成；码 30 分钟有效）。
- KimiCU 版本升级：kimi-cu upgrade。
