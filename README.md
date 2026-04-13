# Krok Helper MVP

把卡拉 OK 字幕视频、原唱无损、伴奏无损准备好后，一次性输出：

- `xxx_on_vocal_hires.mkv`
- `xxx_off_vocal_hires.mkv`

## 项目结构

```text
krok-helper/
├─ app.py                 # 兼容入口，方便直接 python app.py
├─ krok_helper/
│  ├─ __main__.py         # python -m krok_helper
│  ├─ cli.py              # 命令行入口与参数解析
│  ├─ gui.py              # Tkinter 桌面界面
│  ├─ pipeline.py         # 核心处理流程
│  ├─ ffmpeg.py           # ffmpeg / ffprobe 调用封装
│  ├─ models.py           # 数据模型
│  ├─ errors.py           # 自定义异常
│  ├─ config.py           # 配置常量
│  └─ types.py            # 公共类型别名
├─ 启动桌面版.bat
└─ 一键HiRes（mkv）.bat   # 旧脚本，保留参考
```

## 当前版本能做什么

- 读取 1 个字幕视频和 2 个无损音频
- 保留原视频里的非音频流，移除原音轨
- 把外部音频转成 `Hi-Res FLAC 32bit / 2ch`
- 采样率低于 `48kHz` 时自动提升到 `48kHz`
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
  --off-audio "D:\path\inst.flac" `
  --output-dir "D:\path\output"
```

## 依赖

- Python 3.10+
- `ffmpeg`
- `ffprobe`

程序会优先寻找：

- 当前目录下的 `ffmpeg\bin\ffmpeg.exe`
- 当前目录下的 `ffmpeg\bin\ffprobe.exe`

如果没有，再尝试从系统 `PATH` 中寻找。
