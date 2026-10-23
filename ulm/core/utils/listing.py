"""`ulm dl --lists` output: simple table print."""


def list_tracks(tracks: Any) -> None:
    from ulm.core.console import log

    headers = "Type Video/Audio/Subtitles"
    log.success("Loaded tracks:")
    for kind, bucket in (("Video", tracks.videos), ("Audio", tracks.audio), ("Subtitle", tracks.subtitles)):
        for track in bucket:
            log.log("info", str(track))
    if len(tracks.chapters) > 0:
        log.log("info", f"chapters: {len(tracks.chapters)}")
