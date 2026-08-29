# Architecture

PodcastClip is a local-first application. The web dashboard and Chrome extension
collect a YouTube URL, while the Python process running on the user's computer
fetches captions, generates the script, calls TTS, and writes output files.

## Data flow

1. The Chrome extension optionally opens the local dashboard with the current
   YouTube URL pre-filled.
2. The local Python process fetches captions through Supadata or `yt-dlp`, with
   StepFun ASR as an audio fallback.
3. StepFun chat completion produces structured notes and a duration-controlled
   script.
4. StepFun TTS produces MP3 chunks that are stitched locally with `ffmpeg`.
5. PodcastClip stores transcripts, notes, scripts, episode metadata, MP3 files,
   artwork, and `feed.xml` under the local output directory.
6. When the user enables R2, PodcastClip publishes the current episode MP3,
   artwork, and `feed.xml` to that user's bucket. It does not rescan all
   historical MP3 files on each run.

## Storage boundary

Local episode metadata is the source used to rebuild the RSS feed. R2 is a
one-way publishing destination; PodcastClip does not restore local metadata from
R2. The feed includes canonical source YouTube URLs by default and strips all
non-video parameters. `PODCASTCLIP_RSS_INCLUDE_SOURCE_URL=false` replaces item
source links with the feed base URL.

`podcastclip delete <EPISODE_ID>` first tombstones the local metadata, rebuilds
and publishes the feed, and then deletes the episode MP3 from R2. Local episode
files are removed only after the remote operation succeeds, so a failed delete
can be retried with the same command.

R2 is optional. Local MP3 generation works without any R2 configuration. Apple
Podcasts and other remote podcast clients require the feed and audio files to be
available from a publicly reachable host, such as the user's own R2 bucket.
