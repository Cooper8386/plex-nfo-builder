"""ffprobe-backed MediaInfo extraction for the renamer (v0.11.0).

Pulls the codec / quality / dynamic-range / audio info Sonarr's tokens need
out of the actual file rather than the filename. Results are cached per
``(absolute_path, mtime)`` so a rename preview that touches dozens of files
only probes each one once.

Failures are silent on purpose: an unreadable or codec-less file just
yields an empty :class:`MediaInfo` and the renamer falls back to whatever
it can pull from the filename. Renaming is always best-effort.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger


# ---- Codec maps ------------------------------------------------------------

# ffprobe ``codec_name`` -> Sonarr-style label. Anything unmapped is upper-cased.
_VIDEO_CODEC_MAP = {
    "hevc": "h265",
    "h265": "h265",
    "h264": "h264",
    "avc": "h264",
    "av1": "AV1",
    "vp9": "VP9",
    "vp8": "VP8",
    "mpeg2video": "MPEG2",
    "mpeg4": "MPEG4",
    "vc1": "VC1",
    "xvid": "XviD",
}

# Audio codec base mapping. Refined later for DTS/TrueHD variants.
_AUDIO_CODEC_MAP = {
    "eac3": "EAC3",
    "ac3": "AC3",
    "truehd": "TrueHD",
    "dts": "DTS",
    "aac": "AAC",
    "flac": "FLAC",
    "mp3": "MP3",
    "mp2": "MP2",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "alac": "ALAC",
    "pcm_s16le": "PCM",
    "pcm_s24le": "PCM",
    "pcm_s32le": "PCM",
}

# Channel count -> Sonarr style channel string.
_CHANNEL_MAP = {
    1: "1.0",
    2: "2.0",
    3: "2.1",
    4: "4.0",
    5: "4.1",
    6: "5.1",
    7: "6.1",
    8: "7.1",
    10: "9.1",
}

# 3-letter ISO -> 2-letter for Sonarr's [EN+JA] style language tag.
_LANG_3_TO_2 = {
    "eng": "EN", "jpn": "JA", "spa": "ES", "fra": "FR", "fre": "FR",
    "deu": "DE", "ger": "DE", "ita": "IT", "por": "PT", "rus": "RU",
    "kor": "KO", "chi": "ZH", "zho": "ZH", "ara": "AR", "hin": "HI",
    "nld": "NL", "dut": "NL", "swe": "SV", "nor": "NO", "dan": "DA",
    "fin": "FI", "pol": "PL", "tur": "TR", "ces": "CS", "cze": "CS",
    "hun": "HU", "ron": "RO", "rum": "RO", "ell": "EL", "gre": "EL",
    "tha": "TH", "vie": "VI", "ind": "ID", "msa": "MS", "may": "MS",
    "heb": "HE", "ukr": "UK", "bul": "BG", "hrv": "HR", "srp": "SR",
    "slk": "SK", "slo": "SK", "slv": "SL", "lit": "LT", "lav": "LV",
    "est": "ET", "cat": "CA", "und": "",
}


@dataclass
class MediaInfo:
    """Lightweight bag of values needed to render Sonarr templates."""

    video_codec: str = ""        # h265, h264, AV1...
    video_bit_depth: str = ""    # "8", "10", "12"
    hdr_type: str = ""           # HDR10, HDR10Plus, DV, HLG (empty = SDR)
    audio_codec: str = ""        # EAC3, DTS-HD MA, TrueHD Atmos...
    audio_channels: str = ""     # 5.1, 7.1, 2.0...
    audio_languages: str = ""    # "[EN+JA]" style group, empty when unknown
    resolution: str = ""         # 2160p / 1080p / 720p / 480p
    is_3d: bool = False
    languages: list[str] = field(default_factory=list)


# ---- ffprobe ---------------------------------------------------------------

_FFPROBE: Optional[str] = None


def _ffprobe_bin() -> Optional[str]:
    global _FFPROBE
    if _FFPROBE is not None:
        return _FFPROBE or None
    found = shutil.which("ffprobe") or ""
    _FFPROBE = found
    if not found:
        logger.info(
            "ffprobe not found on PATH; MediaInfo tokens will fall back to "
            "filename parsing. Install ffmpeg to enable codec/HDR detection."
        )
    return found or None


# Cache by (path, mtime). Bounded loosely by the number of files in your
# library; a probe is cheap (~50ms) so a stale cache miss is not painful.
_CACHE: dict[tuple[str, int], MediaInfo] = {}


def probe_file(path: Path | str) -> MediaInfo:
    """Run ffprobe against ``path`` and return a :class:`MediaInfo`.

    Any failure (missing ffprobe, unreadable file, malformed json) returns
    an empty :class:`MediaInfo` so callers don't need to catch.
    """
    p = Path(path)
    bin_ = _ffprobe_bin()
    if not bin_ or not p.exists():
        return MediaInfo()
    try:
        mtime = int(p.stat().st_mtime)
    except OSError:
        return MediaInfo()
    key = (str(p), mtime)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [
                bin_, "-v", "quiet", "-print_format", "json",
                "-show_streams", "-show_format", str(p),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            mi = MediaInfo()
        else:
            mi = _parse_probe(json.loads(proc.stdout))
    except Exception as e:  # noqa: BLE001 - never let probing crash a rename
        logger.debug("ffprobe failed for {}: {}", p, e)
        mi = MediaInfo()
    _CACHE[key] = mi
    return mi


def clear_cache() -> None:
    """Forget every cached probe. Cheap; mostly for tests."""
    _CACHE.clear()


# ---- ffprobe JSON parsing --------------------------------------------------


def _parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams", []) or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    mi = MediaInfo()

    if video_streams:
        # Skip embedded thumbnail / mjpeg "video" streams when possible.
        real_vs = [
            s for s in video_streams
            if s.get("codec_name", "").lower() not in ("mjpeg", "png")
        ] or video_streams
        vs = real_vs[0]
        codec_name = (vs.get("codec_name") or "").lower()
        mi.video_codec = _VIDEO_CODEC_MAP.get(codec_name, codec_name.upper())

        # Bit depth via pixel format or bits_per_raw_sample.
        bps = str(vs.get("bits_per_raw_sample") or "").strip()
        pix = (vs.get("pix_fmt") or "").lower()
        if bps and bps not in ("0", ""):
            mi.video_bit_depth = bps
        elif "12" in pix:
            mi.video_bit_depth = "12"
        elif "10" in pix:
            mi.video_bit_depth = "10"
        elif pix:
            mi.video_bit_depth = "8"

        # Resolution bucket.
        try:
            height = int(vs.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        if height >= 2000:
            mi.resolution = "2160p"
        elif height >= 1000:
            mi.resolution = "1080p"
        elif height >= 700:
            mi.resolution = "720p"
        elif height >= 540:
            mi.resolution = "576p"
        elif height >= 400:
            mi.resolution = "480p"

        # Dynamic range. Side-data carries Dolby Vision; color_transfer covers
        # HDR10 / HLG. HDR10+ shows up as a side-data type too.
        side = vs.get("side_data_list") or []
        side_types = " ".join(
            (str(s.get("side_data_type") or "")).lower() for s in side
        )
        color_transfer = (vs.get("color_transfer") or "").lower()
        if "dolby vision" in side_types or "dovi" in side_types:
            mi.hdr_type = "DV"
        elif "hdr10+" in side_types or "hdr dynamic metadata" in side_types:
            mi.hdr_type = "HDR10Plus"
        elif color_transfer == "smpte2084":
            mi.hdr_type = "HDR10"
        elif color_transfer in ("arib-std-b67",):
            mi.hdr_type = "HLG"

        # Stereoscopic 3D (rare but supported by Radarr's {MediaInfo 3D}).
        side_3d = " ".join(
            (str(s.get("side_data_type") or "")).lower() for s in side
        )
        if "stereo3d" in side_3d:
            mi.is_3d = True

    if audio_streams:
        # Prefer the disposition=default track; fall back to first.
        default_track = next(
            (s for s in audio_streams
             if (s.get("disposition") or {}).get("default") == 1),
            audio_streams[0],
        )
        codec_name = (default_track.get("codec_name") or "").lower()
        profile = (default_track.get("profile") or "").lower()
        tags = {
            (k or "").lower(): (v or "")
            for k, v in (default_track.get("tags") or {}).items()
        }
        title_tag = (tags.get("title") or "").lower()

        if codec_name == "dts":
            if "ma" in profile:
                mi.audio_codec = "DTS-HD MA"
            elif "x" in profile:
                mi.audio_codec = "DTS-X"
            elif "hra" in profile or "hd" in profile:
                mi.audio_codec = "DTS-HD"
            elif "es" in profile:
                mi.audio_codec = "DTS-ES"
            else:
                mi.audio_codec = "DTS"
        elif codec_name == "truehd":
            mi.audio_codec = "TrueHD Atmos" if "atmos" in title_tag else "TrueHD"
        elif codec_name == "eac3":
            mi.audio_codec = "EAC3 Atmos" if "atmos" in title_tag else "EAC3"
        else:
            mi.audio_codec = _AUDIO_CODEC_MAP.get(codec_name, codec_name.upper())

        try:
            channels = int(default_track.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        if channels:
            mi.audio_channels = _CHANNEL_MAP.get(channels, f"{channels}.0")

        # Per-track languages, in stream order. Sonarr writes these as
        # ``[EN+JA]`` so we de-duplicate and preserve order.
        seen: list[str] = []
        for s in audio_streams:
            t = (s.get("tags") or {})
            raw = ((t.get("language") or t.get("LANGUAGE")) or "").strip().lower()
            if not raw or raw == "und":
                continue
            iso2 = _LANG_3_TO_2.get(raw, raw.upper()[:2])
            if iso2 and iso2 not in seen:
                seen.append(iso2)
        mi.languages = seen
        if seen:
            mi.audio_languages = "[" + "+".join(seen) + "]"

    return mi


# ---- Convenience helpers ---------------------------------------------------


_SOURCE_NORMALIZE = {
    "WEBDL": "WEBDL", "WEB-DL": "WEBDL", "WEB.DL": "WEBDL",
    "WEBRIP": "WEBRip", "WEB-RIP": "WEBRip", "WEB.RIP": "WEBRip",
    "BLURAY": "Bluray", "BLU-RAY": "Bluray", "BLU.RAY": "Bluray",
    "BDRIP": "Bluray", "BDREMUX": "Remux", "REMUX": "Remux",
    "HDTV": "HDTV", "DVDRIP": "DVD", "DVD-RIP": "DVD", "DVD": "DVD",
    "HDRIP": "HDRip",
}


def build_quality_full(stem: str, mi: MediaInfo) -> str:
    """Synthesize Sonarr's ``Quality Full`` token from filename + ffprobe.

    ``stem`` is the original filename (no extension). We pull source from
    the filename when present (since ffprobe can't tell ``WEB-DL`` from
    ``Bluray``) and use ``mi.resolution`` first, then fall back to a
    resolution found in the filename.
    """
    src = ""
    m = re.search(
        r"\b(WEB[\-. ]?DL|WEB[\-. ]?Rip|Blu[\-. ]?Ray|BDRip|BDRemux|REMUX|HDTV|DVD[\-. ]?Rip|HDRip)\b",
        stem, re.IGNORECASE,
    )
    if m:
        key = m.group(1).replace(" ", "").replace(".", "").upper()
        src = _SOURCE_NORMALIZE.get(key, m.group(1))
    res = mi.resolution
    if not res:
        rm = re.search(r"\b(2160p|1080p|720p|576p|480p)\b", stem, re.IGNORECASE)
        if rm:
            res = rm.group(1).lower()
    parts = [p for p in (src, res) if p]
    return "-".join(parts)


_SONARR_GROUP_RE = re.compile(r"-([A-Za-z0-9_]+)$")
_FANSUB_GROUP_RE = re.compile(r"^\[([^\]]+)\]")


def extract_release_group(stem: str) -> str:
    """Best-effort release group extraction from a filename stem.

    Recognises both Sonarr's ``...-FLUX`` suffix and the fansub
    ``[Group]Title`` prefix. Returns an empty string when nothing matches.
    """
    sonarr = _SONARR_GROUP_RE.search(stem)
    if sonarr:
        candidate = sonarr.group(1)
        # Filter out numeric-only / quality-only matches (e.g. ``-1080p`` or ``-2024``).
        if not re.fullmatch(r"\d{1,4}[pi]?", candidate):
            return candidate
    fansub = _FANSUB_GROUP_RE.match(stem)
    if fansub:
        return fansub.group(1).strip()
    return ""
