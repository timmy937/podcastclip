# Privacy

English | [简体中文](PRIVACY.zh-CN.md)

PodcastClip is a local-first, self-hosted tool. It has no project-operated
account service, analytics service, or shared storage. Using its model,
transcription, and publishing features still sends data to services selected
and configured by the user.

## Data sent to external services

| Recipient | Data sent | When |
| --- | --- | --- |
| YouTube and `yt-dlp` | Video URL; optional browser cookies; captions, metadata, or source audio are requested | For every source without a usable Supadata result |
| Supadata | Source YouTube URL | When `SUPADATA_API_KEY` is configured |
| Text model provider | Source title and transcript or caption text | When producing notes, title translation, and the summary script |
| ASR provider | Source audio and optional language hint | Only when captions are unavailable and ASR fallback is enabled |
| TTS provider | Generated summary script and voice settings | When producing the MP3 |
| User's R2 bucket | Current episode MP3, show artwork, and RSS feed | When R2 storage is enabled |

By default, the RSS feed includes a canonical source YouTube URL containing only
the video ID. Playlist, timestamp, sharing, and tracking parameters are removed.
Set `PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=false` to omit episode source links.
Transcripts, notes, scripts, and episode metadata are not uploaded to R2.

## Public-by-URL podcast feed

An R2 development URL or public custom domain is unlisted, not private. Anyone
who obtains the feed or MP3 URL may be able to access it. Podcast clients may
download and cache copies that cannot be revoked by deleting the R2 object.
When source links are enabled, anyone who reads the feed can also see each source
video ID; this can expose an unlisted video.

Do not process or publish material that requires access control. This version
does not provide an authenticated podcast feed.

## Local and remote deletion

Run `podcastclip delete <EPISODE_ID>` using the metadata filename under
`output/episodes/` without `.json`. The command removes the episode from the
feed, publishes the updated feed, deletes the corresponding R2 MP3, and then
removes local generated files. A failed R2 deletion leaves tombstoned metadata
and local files so the command can be retried.

Deleting an episode cannot remove copies already downloaded by Apple Podcasts,
other podcast clients, caches, or external model providers.

## Provider retention and training

PodcastClip cannot control how a user-selected provider retains, trains on, or
deletes submitted data. Review the current privacy and data-processing terms of
each configured model provider, Supadata, YouTube, and Cloudflare before use.
Use provider-side deletion controls when required.

Remote Base URLs must use HTTPS. HTTP is allowed only for loopback services,
such as a model server running on the same computer.

## Cookies and secrets

Browser cookies are optional and are passed only to the isolated `yt-dlp`
process when explicitly requested. API keys, cookies, transcripts, scripts,
metadata, generated audio, and unlisted feed URLs must not be committed to Git.
