# 直播守候（Stream Keeper）

面向普通用户的抖音、小红书直播自动录制桌面工具。粘贴直播间链接后，程序会定期检查开播状态，开播后使用 FFmpeg 自动保存为 MP4，并记录录制历史。

## 功能

- 仅支持抖音与小红书，自动识别平台并拒绝其他链接
- 多直播间循环监测，开播后自动录制
- 自带 FFmpeg，无需用户单独安装
- 自定义保存目录、画质、检查间隔和分段时长
- 录制状态、持续时间、错误信息和历史记录
- 可随时停止某一路录制，退出程序时安全结束 FFmpeg
- Cookie 只保存在本机应用数据目录，不写入源码或安装包
- macOS `.dmg` 与 Windows 安装程序构建流程

## 本地运行

需要 Python 3.10 或以上版本，推荐使用 `uv`：

```bash
uv sync --extra dev
uv run python run.py
```

应用数据位置：

- macOS：`~/Library/Application Support/StreamKeeper/`
- Windows：`%APPDATA%\StreamKeeper\`

## 打包

```bash
uv sync --extra dev
uv run python scripts/build.py
```

macOS 会生成 `.app`，并在系统提供 `hdiutil` 时生成 `.dmg`。Windows 会生成可运行目录和 ZIP；安装版由 GitHub Actions 使用 Inno Setup 生成。

## 安全说明

不要把自己的 Cookie 提交到 Git。录制仅用于你有权保存的直播内容。平台页面或接口变化可能导致解析暂时失效。

## 开源来源

直播地址解析代码基于 [ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder)，原项目使用 MIT License。原许可证保存在 `third_party/upstream/LICENSE`。

