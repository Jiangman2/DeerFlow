# DeerFlow 技术播客

这是由 `docs/deerflow-internals-guide-zh.md` 生成的分集式中文技术播客。

## 当前产物

- [分集讲稿清单](scripts/README.md)
- [第 1 集 MP3 样片](audio/episode-01.mp3)
- 第 1 集时长约 19 分 54 秒。
- 音色：Windows 本地中文语音 `Microsoft Huihui Desktop`。
- 音频：MP3，96 kbps。

## 分集原则

- 超长的基础知识和底层逻辑章节按主题拆成多个部分。
- Mermaid 图转换为“图示口述”，不会直接朗读图形代码。
- 代码块转换成用途和调用关系说明，不逐字朗读代码。
- 表格转换为连续的对照说明。
- 每集包含标题、开场、章节内容和小结。

## 重新生成讲稿

```powershell
python scripts/build_deerflow_podcast.py
```

## 生成全部音频

```powershell
powershell -ExecutionPolicy Bypass -File scripts/synthesize_deerflow_podcast.ps1
```

## 生成指定一集

```powershell
powershell -ExecutionPolicy Bypass -File scripts/synthesize_deerflow_podcast.ps1 -Episode 1
```

音频合成脚本使用本地 Windows Speech API 和 FFmpeg，不需要把电子书内容上传到外部服务。自动生成后仍建议人工试听，尤其是英文缩写、文件路径、模型名和代码术语。
