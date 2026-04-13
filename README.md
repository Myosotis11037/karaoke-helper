# Krok Helper

把卡拉 OK 字幕视频、原唱无损、伴奏无损准备好后，一次性输出：

- `on_vocal.mkv`
- `off_vocal.mkv`

## 项目结构

```text
krok-helper/
├─ app.py
├─ krok_helper/
│  ├─ __main__.py
│  ├─ cli.py
│  ├─ config.py
│  ├─ errors.py
│  ├─ ffmpeg.py
│  ├─ gui.py
│  ├─ models.py
│  ├─ pipeline.py
│  ├─ types.py
│  └─ windows.py
├─ 启动桌面版.bat
└─ 一键HiRes（mkv）.bat
```

## 当前版本能做什么

- 读取 1 个字幕视频和 2 个无损音频
- 支持点击选择文件，也支持把文件直接拖进三个大卡片
- 支持选择 `ffmpeg` 所在目录
- 保留原视频里的非音频流，移除原音轨
- 把外部音频转成 `Hi-Res FLAC 32bit / 2ch`
- 采样率低于 `48kHz` 时自动提升到 `48kHz`
- 自动把输出文件放到字幕视频所在目录
- 输出两个最终可上传版本
- 提供图形界面和命令行两种用法

## 启动方式

双击 `启动桌面版.bat`

或者在当前目录运行：

```powershell
python -m krok_helper
```

兼容旧入口：

```powershell
python app.py
```

## 命令行用法

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

## 依赖

- Python 3.10+
- `ffmpeg`
- `ffprobe`

程序查找 `ffmpeg` / `ffprobe` 的顺序是：

1. 系统环境变量 `PATH`
2. 你在界面或命令行里指定的 `ffmpeg` 目录

提示：

- 目录可以直接选到 `ffmpeg\bin`
- 也可以选 `ffmpeg` 根目录，程序会自动尝试 `bin\ffmpeg.exe`

## 说明

- 这版以你的固定工作流为主。
- 如果字幕视频和音频时长差异较大，程序会给出警告，但仍继续处理。
- B 站是否最终显示 Hi-Res，上传时仍需要你在投稿页手动勾选对应选项。
