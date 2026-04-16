# Krok Helper

把卡拉 OK 字幕视频、原唱无损、伴奏无损准备好后，一次性输出两个最终文件：

- `on_vocal.mkv`
- `off_vocal.mkv`

也支持改成你自己定义的命名范式，例如：

- `{video_name}_orig_master.mkv`
- `{video_name}_inst_master.mkv`

## 当前版本能做什么

- 读取 1 个字幕视频和 2 个音频文件
- 支持点击选择文件，也支持把文件直接拖进三个大卡片
- 提供“波形对齐”模块，可以把字幕视频音轨和原唱音源画成双波形
- 波形对齐支持两种目标：调整字幕视频，或调整原唱音源并导出修正后的音频
- 自动把外部音频标准化为 `Hi-Res FLAC / 2ch`
- 原唱和伴奏格式不一致时也能处理，例如 `flac + wav`
- 自动保留原视频里的非音频流，并移除原音轨
- 采样率低于 `48kHz` 时自动提升到 `48kHz`
- 自动把输出文件放到字幕视频所在目录
- 支持图形界面和命令行两种用法
- 提供单独的“设置”窗口
- 支持自定义输出命名模板
- 支持把命名设置和 `ffmpeg` 目录保存到本地，下次启动自动加载

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

- “波形对齐”：先放入字幕视频和原唱音源，生成波形后选择“调整字幕视频”或“调整原唱音源”。调整时可以拖动波形上的播放头选择预览起点，同时听字幕视频音轨和原唱音源来确认是否对齐；未生成波形时按空格会生成波形，生成后按空格可以切换播放/停止，`Ctrl+S` 会导出当前对齐目标。调整字幕视频时，负偏移会在导出时裁掉视频开头；正偏移会先尝试按源视频参数生成黑场并 `concat -c copy` 无损拼接，失败或检测到丢流时自动回退到重编码补黑。调整原唱音源时，负偏移会裁掉音频开头，正偏移会给音频前面补静音；导出结果统一默认为 WAV PCM 无损音频。
- 正偏移需要回退到重编码补黑时，可以选择“软编省空间”或“硬编快速”。硬编会优先使用 NVENC（例如 `hevc_nvenc` / `h264_nvenc`），速度更快但文件可能更大；不可用时会自动改用软编。
- “Hi-Res 生成”：选择字幕视频、原唱无损、伴奏无损，并开始最终封装导出。

“设置”窗口会按当前模块显示对应选项：

- `FFmpeg` 目录
- Hi-Res 生成: 输出命名模式、原唱输出模板、伴奏输出模板
- 波形对齐: 对齐后视频命名模板、对齐后音频命名模板

波形对齐的默认命名是：

- 对齐后视频: `{video_name}_aligned.mp4`
- 对齐后音频: `{audio_name}_aligned.wav`

这些设置保存后会写入本地 `settings.json`，下次启动会自动加载。

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
%APPDATA%\Krok Helper\settings.json
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
dist\windows\Krok Helper\
```

主程序：

```text
dist\windows\Krok Helper\Krok Helper.exe
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
dist/macos/Krok Helper.app
```

## 依赖

- Python 3.10+
- `ffmpeg`
- `ffprobe`

## 说明

- 如果字幕视频和音频时长差异较大，程序会给出警告，但仍会继续处理
- 当前流程会先把输入音频标准化，再封装进最终 `mkv`
- B 站是否最终显示 Hi-Res，上传时仍需要你在投稿页手动勾选对应选项
