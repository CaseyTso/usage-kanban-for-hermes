# 视觉验收备注（视觉模型选型）

开发期视觉验收（读截图）用到的视觉模型经验，记录如下。

已实测验证（2026-08）：

- **mimo-v2.5-pro（opencode-go）不支持图像输入**：zen/go/v1/chat/completions 返回 400
  "No endpoints found that support image input"。不能用于读图验收。
- **智谱 glm-4v-flash（免费）**：可用但 OCR 弱，小字会漏读/幻觉，只适合粗看。
- **SiliconFlow Qwen/Qwen3-VL-32B-Instruct（推荐）**：OCR 强、逐字抄录可靠，单次约 1 万 token、
  费用几分钱。需要用户自己的 SiliconFlow API key（不落仓库）。
- **opencode-go 接口**：额度接口 GET https://opencode.ai/zen/go/v1/usage；chat completions 走
  POST https://opencode.ai/zen/go/v1/chat/completions（Authorization: Bearer <key>，
  OpenAI 兼容消息格式，图像用 data:image/...;base64 的 image_url）。
- **必须带浏览器 UA**：opencode.ai 有 Cloudflare 防护，python-urllib 默认 UA 会被 403/error 1010
  拒绝；用 curl 或带 Mozilla UA 的请求即可。
- 调用会消耗 opencode-go 订阅额度，验收调用尽量少、图尽量压缩。
- 截图方式：终端需屏幕录制权限；screencapture -x 全屏 / -l <windowID> 按窗口；
  窗口 ID 用 swift CGWindowList 枚举。裁剪后放大 2 倍再送模型，效果更好。
