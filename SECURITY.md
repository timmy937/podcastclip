# Security

## Supported version

Security fixes are currently applied to the latest release.

## Local web server

PodcastClip's built-in web dashboard has no user-account authentication. It binds to
`127.0.0.1` by default and rejects all non-loopback binding and request hosts.
State-changing requests also require same-origin browser metadata, JSON where
applicable, and a random in-memory API token injected into the dashboard.
Remote binding is not available in v0.1.

The protection is for a single-user local tool; it is not a multi-user account
or authorization system. Do not place a reverse proxy in front of the built-in
server or expose it to another machine.

## External processes and endpoints

PodcastClip validates YouTube hosts before invoking `yt-dlp`, ignores user and
system `yt-dlp` configuration, and separates options from the URL. Keep
`yt-dlp` at or above the minimum version declared in `pyproject.toml`.

Remote model, Supadata, and R2 Base URLs must use HTTPS. HTTP is accepted only
for loopback services such as a model server running on the same computer.

## Secrets and generated data

Keep API keys, R2 credentials, browser cookies, transcripts, scripts, metadata,
and generated audio out of Git. Store credentials in an untracked `.env` file
and review `git status` before every commit.

An R2 public development URL or custom public domain is unlisted, not private.
Anyone who knows the feed or media URL may be able to read it. The RSS feed does
publish a canonical source YouTube URL by default; set
`PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=false` to hide episode source links.

See `PRIVACY.md` for the complete external data flow and deletion behavior.

## Reporting a vulnerability

Use the repository's private security advisory feature to report a
vulnerability. Do not include API keys, cookies, unlisted feed URLs, transcripts,
or other personal data in a public issue.
