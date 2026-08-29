# 隐私说明

[English](PRIVACY.md) | 简体中文

PodcastClip 是本地优先、自行托管的工具。项目方不运营账号系统、分析服务或共享存储，但在使用模型、转录和发布能力时，程序仍会把数据发送给用户选择并配置的第三方服务。

## 会发送给第三方的数据

| 接收方 | 发送的数据 | 触发条件 |
| --- | --- | --- |
| YouTube 与 `yt-dlp` | 视频 URL；可选浏览器 Cookie；请求字幕、metadata 或原始音频 | Supadata 没有返回可用结果时 |
| Supadata | 原始 YouTube URL | 配置了 `SUPADATA_API_KEY` 时 |
| 文本模型服务 | 原始标题、转录稿或字幕文本 | 生成笔记、标题翻译和摘要脚本时 |
| ASR 服务 | 原始音频和可选语言提示 | 没有字幕且启用了 ASR 回退时 |
| TTS 服务 | 生成后的摘要脚本和音色设置 | 生成 MP3 时 |
| 用户自己的 R2 | 本次生成的 MP3、节目封面和 RSS Feed | 启用 R2 存储时 |

RSS Feed 默认包含只保留视频 ID 的规范 YouTube 来源地址，播放列表、时间戳、分享参数和跟踪参数都会被删除。设置 `PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=false` 可以关闭单集来源链接。转录稿、笔记、脚本和节目 metadata 不上传 R2。

## 可凭 URL 访问的 Feed

R2 开发地址或公开自定义域名属于“不公开列出”，并不是真正私密。获得 Feed 或 MP3 URL 的人可能访问对应内容。播客客户端还可能下载并缓存副本，删除 R2 对象不能撤回这些副本。

启用来源链接时，能够读取 Feed 的人也能看到每一期的原视频 ID，这可能暴露未公开视频。

不要用当前版本处理或发布必须实施访问控制的内容。当前版本不提供带身份验证的播客 Feed。

## 本地与远端删除

找到 `output/episodes/` 下的 metadata 文件名，去掉 `.json` 后运行 `podcastclip delete <EPISODE_ID>`。程序会把该期从 Feed 中移除并上传新 Feed，删除对应的 R2 MP3，最后清理本地生成文件。R2 删除失败时，程序保留带删除标记的 metadata 和本地文件，以便使用相同命令重试。

删除节目无法清除 Apple Podcasts、其他播客客户端、缓存或外部模型服务已经保存的副本。

## 第三方保留与训练政策

PodcastClip 无法控制用户所选服务如何保存、训练或删除提交的数据。使用前应查看当前模型服务、Supadata、YouTube 和 Cloudflare 的隐私与数据处理条款，并在需要时使用服务商提供的删除能力。

公网 Base URL 必须使用 HTTPS；只有同一台电脑上的 localhost 或 loopback 服务可以使用 HTTP。

## Cookie 与密钥

浏览器 Cookie 是可选项，只有用户明确启用时才会传给隔离运行的 `yt-dlp` 进程。API Key、Cookie、转录稿、脚本、metadata、生成音频和不公开列出的 Feed URL 都不应提交到 Git。
