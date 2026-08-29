# PodcastClip

[English](README.md) | 简体中文

**把一段太长、没时间完整收听的 YouTube 播客，压缩成 2、5、8 或 15 分钟的音频摘要，再放进 Apple Podcasts 里听。**

PodcastClip 在你的电脑上运行整条制作流程：获取字幕或转录稿，生成中文或英文摘要脚本和 MP3，再把节目发布到你自己的 Cloudflare R2 RSS。只需在 Apple Podcasts 中添加一次 Feed，之后生成的摘要会持续出现在同一个节目中。

```text
YouTube 播客链接
      ↓
字幕或 ASR 转录稿
      ↓
结构化摘要和短脚本
      ↓
中文或英文 MP3
      ↓
用户自己的 R2：MP3 + 封面 + feed.xml
      ↓
iPhone / Apple Watch 上的 Apple Podcasts
```

这个仓库只做播客转换和投递，不负责发现播客，也不会把节目提交到 Apple Podcasts 的公开节目目录。

## 界面预览

### 本地网页

![PodcastClip 本地网页](docs/images/web-dashboard.png)

### Chrome 插件

![PodcastClip Chrome 插件](docs/images/chrome-extension.png)

## 推荐组合

| 环节 | 推荐选择 | 要求 |
| --- | --- | --- |
| 文本摘要、ASR、TTS | StepFun | 推荐，因为一个订阅可以覆盖三种能力 |
| 读取 YouTube 已有字幕 | Supadata | 可选，推荐用于提速 |
| 创建和管理任务 | 本地 PodcastClip 网页 | 必需 |
| 提交浏览器当前页面 | Chrome 插件 | 可选，推荐 |
| 托管 MP3、封面和 RSS | 用户自己的 Cloudflare R2 | Apple Podcasts 投递必需 |
| 收听 | Apple Podcasts | 主要终端 |

StepFun 不是必需的大模型提供商。当前 v0.1 已针对 StepFun 完成接入和验证；其他服务需要提供兼容的文本、语音转文字和文字转语音接口及模型名称，不兼容的接口需要单独实现适配器。

## 标准配置流程

### 1. 安装 PodcastClip

需要：

- Python 3.11 或更高版本
- Git
- `ffmpeg` 和 `ffprobe`

```bash
git clone https://github.com/timmy937/podcastclip.git
cd podcastclip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

### 2. 配置模型 API

编辑 `.env`，替换其中的 Key：

```env
# 推荐：一个 StepFun 账户覆盖摘要、ASR 和 TTS
STEPFUN_CHAT_API_KEY=<YOUR_STEPFUN_API_KEY>
STEPFUN_CHAT_BASE_URL=https://api.stepfun.com/step_plan/v1
STEPFUN_CHAT_MODEL=step-3.5-flash-2603
STEPFUN_TTS_MODEL=stepaudio-2.5-tts
STEPFUN_ASR_MODEL=stepaudio-2.5-asr
STEPFUN_TTS_VOICE=zixinnansheng

# 可选（推荐）：优先读取已有字幕，避免不必要的音频 ASR
SUPADATA_API_KEY=<YOUR_SUPADATA_API_KEY>
SUPADATA_BASE_URL=https://api.supadata.ai/v1
```

默认情况下，音频模型复用 `STEPFUN_CHAT_API_KEY` 和 `STEPFUN_CHAT_BASE_URL`。只有 ASR 或 TTS 使用另一个兼容账户时，才需要设置 `STEPFUN_AUDIO_API_KEY` 和 `STEPFUN_AUDIO_BASE_URL`。

### 3. 配置用户自己的 R2

在 Cloudflare 中创建：

1. 一个 R2 bucket。
2. 一组限制到该 bucket 的读写 API 凭证。
3. 一个公开的 R2 开发地址或自定义域名。

然后把这些值写入 `.env`：

```env
PODCASTCLIP_STORAGE_BACKEND=r2
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_BUCKET=<YOUR_BUCKET_NAME>
R2_ACCESS_KEY_ID=<YOUR_R2_ACCESS_KEY_ID>
R2_SECRET_ACCESS_KEY=<YOUR_R2_SECRET_ACCESS_KEY>
R2_REGION=auto
R2_PREFIX=podcastclip
R2_PUBLIC_BASE_URL=https://<YOUR_PUBLIC_R2_DOMAIN>

# 默认：每一期 RSS 都链接回规范化后的原始视频
PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=true
```

PodcastClip 不创建共享 bucket，也不提供 R2 凭证。每个用户都使用自己的 bucket 和 Key；Cloudflare 网页配置完成后，只需要填写 `.env`，不需要修改代码。

### 4. 启动网页

```bash
source .venv/bin/activate
podcastclip web
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)，粘贴 YouTube 链接，选择输出语言和时长，然后提交任务。

v0.1 网页只允许本机使用。它没有用户账号系统，只接受 loopback 请求，也不支持绑定远程网卡。

### 5. 在 Apple Podcasts 中添加 RSS

第一次任务成功后，Feed 地址是：

```text
<R2_PUBLIC_BASE_URL>/<R2_PREFIX>/feed.xml
```

在 iPhone 上打开 Apple Podcasts，使用“按 URL 关注节目”一类入口粘贴 Feed 地址。这个操作只需要做一次。以后每次任务成功都会更新同一个 Feed，Apple Podcasts 会在下一次刷新后显示新节目。

这个 Feed 是“不在目录中公开列出”，不等于私密。获得 Feed 或 MP3 地址的人仍可能访问其中的内容。

## 日常使用

1. 运行 `podcastclip web`。
2. 粘贴 YouTube 播客链接，或者通过 Chrome 插件提交当前页面。
3. 等待转录、摘要、TTS 和 R2 上传完成。
4. 打开 Apple Podcasts 收听新节目。

## 时长规则

| 预设 | 建议原播客时长 | 目标 MP3 | 适合场景 |
| --- | ---: | ---: | --- |
| `quick` | 30 分钟以内 | 2 分钟 | 只了解核心结论 |
| `standard` | 30-60 分钟 | 5 分钟 | 日常默认（推荐） |
| `deep` | 60-120 分钟 | 8 分钟 | 保留更多论据、案例和嘉宾观点 |
| `long` | 120 分钟以上 | 15 分钟 | 长访谈、课程或高信息密度内容 |

原播客时长只是档位建议，不是硬限制；信息密度特别高时可以上调一档。节目开头的口播介绍不计入正文目标时长，每一期结尾都会总结三个可以带走的要点。

输出语言是显式选项。v0.1 提供中文和英文，默认中文。

## RSS 原视频链接

默认情况下，每一期 RSS 都会链接回原始 YouTube 视频。PodcastClip 只保存这种规范地址：

```text
https://www.youtube.com/watch?v=<VIDEO_ID>
```

播放列表位置、时间戳、分享参数和跟踪参数都会被删除。这样既能在 Apple Podcasts 中找到来源，也不会发布用户最初粘贴的完整 URL。

如果不希望 Feed 包含来源链接，可以设置：

```env
PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=false
```

关闭后，每个 RSS 单集的链接会回退到 Feed 根地址。即使经过规范化，未公开视频链接仍会向能够读取 Feed 的人暴露视频 ID。

## R2 会收到什么

每次任务成功后只上传：

- 本次任务生成的 MP3
- 节目封面
- 更新后的 `feed.xml`

PodcastClip 不会在每次运行时扫描并重新上传全部历史 MP3。转录稿、笔记、生成脚本和节目 metadata 都保留在本机 `output/`。只有在 `PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=true` 时，规范化后的原视频地址才会出现在 `feed.xml` 中。

处理过程仍在本机完成：转录编排、摘要、TTS 和 RSS 生成都运行在启动 PodcastClip 的电脑上。R2 只负责存储与投递，不负责云端计算。

## Chrome 插件

可选插件位于 [`extension/`](extension/)：

1. 启动本地网页。
2. 打开 `chrome://extensions`。
3. 开启“开发者模式”。
4. 选择“加载已解压的扩展程序”，然后选择仓库中的 `extension/` 目录。
5. 打开一个 YouTube 视频并点击插件。

插件只会打开本地网页并自动填入当前 URL。它不保存 API Key，也不会直接调用模型服务、Supadata 或 R2。

## 可选模式

### 只生成本地 MP3

测试阶段或者不需要 Apple Podcasts 投递时，可以使用本地模式：

```env
PODCASTCLIP_STORAGE_BACKEND=local
```

生成文件保存在 `output/`。电脑或本地网页关闭后，iPhone 无法继续读取这个本地 RSS。

### 命令行

```bash
podcastclip run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --duration deep \
  --target-language en
```

部分视频需要登录后的 YouTube 会话。CLI 支持 `--cookies-from-browser` 或 `--cookies`，以及 `yt-dlp` 所需的运行时参数。不要把 Cookie 文件提交到 Git。

## 输出和删除

```text
output/
├── artwork/
├── episodes/
│   ├── YYYY-MM-DD-英文原始标题 - 中文标题.mp3
│   └── *.json
├── text/
│   ├── *-transcript.txt
│   ├── *-overview.json
│   ├── *-notes.txt
│   └── *-script.txt
└── feed.xml
```

删除某一期时，使用对应 metadata 文件名并去掉 `.json`：

```bash
podcastclip delete "<EPISODE_ID>"
```

在 R2 模式下，PodcastClip 会先从 Feed 移除该节目并发布更新后的 Feed，再删除远端 MP3，最后清理对应的本地文件。如果 R2 操作失败，重新运行相同命令即可；远端删除成功前，本机会保留带删除标记的 metadata。

## 文档

- [架构说明](docs/architecture.md)
- [隐私和外部数据流](PRIVACY.zh-CN.md)
- [安全边界](SECURITY.md)

## 许可证

PodcastClip 使用 [MIT License](LICENSE) 开源。
