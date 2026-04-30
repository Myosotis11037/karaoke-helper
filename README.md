# Karaoke Helper

当前版本：`2.0.2`

把卡拉 OK 字幕视频、原唱音频、伴奏音频准备好后，一次性输出两个最终文件：

- `on_vocal.mkv`
- `off_vocal.mkv`

也支持改成你自己定义的命名范式，例如：

- `{video_name}_orig_master.mkv`
- `{video_name}_inst_master.mkv`

## 当前版本能做什么

- 提供两个核心模块：“波形对齐”和“Hi-Res 生成”
- 支持点击选择文件，也支持把文件直接拖进文件卡片，并在拖入时给出高亮反馈
- “波形对齐”可以把字幕视频音轨和原唱音源画成双波形，支持自动对齐、播放预览、点击时间栏跳转播放进度、跳到开头 / 末尾
- 波形对齐支持两种目标：调整字幕视频，或调整原唱音源并导出修正后的音频
- 调整字幕视频时，支持开头裁剪、前导画面补黑 / 补白 / 首帧定格、视频尾裁、额外导出对齐后的 WAV，以及导出时统一重编码
- 调整原唱音源时，会导出修正后的无损 WAV 音频；正偏移补静音，负偏移裁掉音频开头
- “Hi-Res 生成”支持字幕视频、原唱音频、伴奏音频三路输入；原唱 / 伴奏输入支持常见音频格式，也支持带音轨的 `mkv` / `mp4`
- 自动把外部音频标准化为 `Hi-Res FLAC / 2ch`，采样率低于 `48kHz` 时自动提升到 `48kHz`
- 自动保留原视频里的非音频流，并移除原音轨
- 支持图形界面和命令行两种用法
- 支持设置窗口、自定义命名模板、保存 `ffmpeg` 目录和本地偏好设置

## 项目结构

```text
krok-helper/
├─ app.py
├─ README.md
├─ krok_helper/
│  ├─ __init__.py
│  ├─ __main__.py
│  ├─ cli.py
│  ├─ config.py
│  ├─ errors.py
│  ├─ ffmpeg.py
│  ├─ gui.py
│  ├─ models.py
│  ├─ pipeline.py
│  ├─ settings.py
│  ├─ types.py
│  └─ windows.py
├─ scripts/
│  ├─ build_macos.command
│  └─ build_windows.bat
├─ 启动桌面版.bat
└─ 一键HiRes（mkv）.bat
```

## 启动方式

双击：

```text
启动桌面版.bat
```

或者在当前目录运行：

```powershell
python -m krok_helper
```

兼容旧入口：

```powershell
python app.py
```

## 图形界面说明

主界面左侧可以切换模块：

- “波形对齐”：先放入字幕视频和原唱音源，生成波形后选择“调整字幕视频”或“调整原唱音源”。可以用“自动对齐”先估算偏移，再拖动波形、点击时间栏、点击波形区来切换预览位置，同时听字幕视频音轨和原唱音源确认是否对齐。未生成波形时按空格会生成波形，生成后按空格可以切换播放 / 停止，`Alt+V` 切换拖动模式，`Ctrl+D` 自动对齐，`Ctrl+S` 导出当前对齐目标。
- 调整字幕视频时，支持开头裁剪、前导画面、尾裁、额外导出 WAV、导出视频重编码为 `1080p 60fps`、导出视频时自动裁到音频末尾等选项；前导画面支持前黑、前白和首帧定格。
- 调整原唱音源时，界面会自动禁用不适用的视频选项，导出结果统一默认为 WAV PCM 无损音频。
- “Hi-Res 生成”：选择字幕视频、原唱音频、伴奏音频，并开始最终封装导出。原唱音频和伴奏音频都支持导入常见音频文件，也支持导入带音轨的 `mkv` / `mp4`。

## 输出命名

默认命名始终保留：

- `on_vocal.mkv`
- `off_vocal.mkv`

如果切换到“自定义模板”，可以分别设置：

- 原唱模板
- 伴奏模板

目前支持的占位符：

- `{video_name}`: 字幕视频文件名，不含扩展名

示例：

```text
原唱模板: {video_name}_karaoke_on
伴奏模板: {video_name}_karaoke_off
```

最终会输出：

```text
你的视频名_karaoke_on.mkv
你的视频名_karaoke_off.mkv
```

注意：

- 模板里不需要写 `.mkv`
- 模板不能包含路径分隔符
- 如果模板非法，程序会在开始处理前给出错误提示

波形对齐的命名模板同样不需要写扩展名。对齐后视频模板支持 `{video_name}`；对齐后音频模板支持 `{audio_name}` 和 `{video_name}`。

## 命令行用法

最基本用法：

```powershell
python -m krok_helper `
  --video "D:\path\subtitle_video.mkv" `
  --on-audio "D:\path\song.flac" `
  --off-audio "D:\path\inst.flac"
```

如果系统 `PATH` 里没有 `ffmpeg` / `ffprobe`，可以额外指定目录：

```powershell
python -m krok_helper `
  --video "D:\path\subtitle_video.mkv" `
  --on-audio "D:\path\song.flac" `
  --off-audio "D:\path\inst.flac" `
  --ffmpeg-dir "D:\tools\ffmpeg\bin"
```

如果想通过命令行指定自定义命名：

```powershell
python -m krok_helper `
  --video "D:\path\subtitle_video.mkv" `
  --on-audio "D:\path\song.flac" `
  --off-audio "D:\path\inst.flac" `
  --output-name-mode template `
  --on-name-template "{video_name}_orig_master" `
  --off-name-template "{video_name}_inst_master"
```

支持的命名模式：

- `fixed`
- `template`

如果没有显式传入命名参数或 `ffmpeg` 目录参数，命令行会优先尝试读取本地保存的设置。

## FFmpeg 目录说明

程序查找 `ffmpeg` / `ffprobe` 的顺序是：

1. 系统环境变量 `PATH`
2. 你在设置窗口或命令行里指定的 `ffmpeg` 目录

推荐直接选择：

```text
...\ffmpeg\bin
```

例如：

```text
D:\tools\ffmpeg\bin
```

也可以选择 `ffmpeg` 根目录，程序会自动尝试：

- `bin\ffmpeg.exe`
- `bin\ffprobe.exe`

## 本地设置保存位置

Windows 下默认保存到：

```text
%APPDATA%\Karaoke Helper\settings.json
```

目前会保存：

- 输出命名模式
- 原唱模板
- 伴奏模板
- `ffmpeg` 目录

## 打包

打包使用 `PyInstaller`。

### Windows

直接双击运行：

```text
scripts\build_windows.bat
```

或者在终端中运行：

```powershell
.\scripts\build_windows.bat
```

输出目录：

```text
dist\windows\Karaoke Helper\
```

主程序：

```text
dist\windows\Karaoke Helper\Karaoke Helper.exe
```

### macOS

先给脚本执行权限：

```bash
chmod +x ./scripts/build_macos.command
```

然后运行：

```bash
./scripts/build_macos.command
```

输出目录：

```text
dist/macos/Karaoke Helper.app
```

## 依赖

- Python 3.10+
- `ffmpeg`
- `ffprobe`

## 说明

- 如果字幕视频和音频时长差异较大，程序会给出警告，但仍会继续处理
- 当前流程会先把输入音频标准化，再封装进最终 `mkv`
- B 站是否最终显示 Hi-Res，上传时仍需要你在投稿页手动勾选对应选项
