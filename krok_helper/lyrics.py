from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from krok_helper.config import APP_NAME, APP_VERSION


LYRICS_PREVIEW_LINE = "line"
LYRICS_PREVIEW_VERBATIM = "verbatim"
DEFAULT_LYRICS_PROVIDER_IDS = ("lrclib",)
DEFAULT_LYRICS_SEARCH_LIMIT = 30

_TIMESTAMP_PATTERN = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{2,3}))?\]")
_TOKEN_SPLIT_PATTERN = re.compile(r"[\s\-_/\\|,.;:!?()\[\]{}'\"`~]+")


class LyricsSearchError(RuntimeError):
    """Raised when every configured lyrics provider fails."""


@dataclass(slots=True)
class LyricsSearchCandidate:
    provider_id: str
    provider_name: str
    track_id: str
    title: str
    artist: str
    album: str
    duration_seconds: float | None
    plain_lyrics: str
    synced_lyrics: str
    source_url: str | None = None
    title_score: float = 0.0
    artist_score: float = 0.0
    album_score: float = 0.0
    lyrics_score: float = 0.0
    display_score: float = 0.0
    match_source: str = ""

    @property
    def has_synced_lyrics(self) -> bool:
        return bool(self.synced_lyrics.strip())

    @property
    def best_available_lyrics(self) -> str:
        return self.synced_lyrics.strip() or self.plain_lyrics.strip()


@dataclass(slots=True, frozen=True)
class LyricsPreview:
    text: str
    used_synced_lyrics: bool
    used_estimated_char_timing: bool


@dataclass(slots=True, frozen=True)
class ParsedLrcLine:
    start_ms: int
    text: str


class LyricsProvider(Protocol):
    provider_id: str
    provider_name: str

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT) -> list[LyricsSearchCandidate]:
        ...


class LrclibLyricsProvider:
    provider_id = "lrclib"
    provider_name = "LRCLIB"

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT) -> list[LyricsSearchCandidate]:
        params = urlencode({"q": keyword})
        request = Request(
            f"https://lrclib.net/api/search?{params}",
            headers={
                "Accept": "application/json",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise LyricsSearchError(f"LRCLIB 请求失败: HTTP {exc.code}") from exc
        except URLError as exc:
            raise LyricsSearchError(f"LRCLIB 网络错误: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LyricsSearchError("LRCLIB 请求超时") from exc
        except json.JSONDecodeError as exc:
            raise LyricsSearchError("LRCLIB 返回了无法解析的数据") from exc

        if not isinstance(payload, list):
            raise LyricsSearchError("LRCLIB 返回数据格式异常")

        items: list[LyricsSearchCandidate] = []
        for entry in payload[:limit]:
            if not isinstance(entry, dict):
                continue
            items.append(
                LyricsSearchCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    track_id=str(entry.get("id") or ""),
                    title=str(entry.get("trackName") or entry.get("name") or "").strip(),
                    artist=str(entry.get("artistName") or "").strip(),
                    album=str(entry.get("albumName") or "").strip(),
                    duration_seconds=_coerce_float(entry.get("duration")),
                    plain_lyrics=str(entry.get("plainLyrics") or "").strip(),
                    synced_lyrics=str(entry.get("syncedLyrics") or "").strip(),
                )
            )
        return items


class LyricsSearchService:
    def __init__(self, providers: list[LyricsProvider] | None = None) -> None:
        self.providers = providers or [LrclibLyricsProvider()]

    def search(
        self,
        keyword: str,
        *,
        provider_ids: tuple[str, ...] = DEFAULT_LYRICS_PROVIDER_IDS,
        limit: int = DEFAULT_LYRICS_SEARCH_LIMIT,
    ) -> list[LyricsSearchCandidate]:
        normalized_keyword = " ".join(keyword.split())
        if not normalized_keyword:
            return []

        allowed_providers = [provider for provider in self.providers if provider.provider_id in provider_ids]
        if not allowed_providers:
            raise LyricsSearchError("没有可用的歌词来源。")

        all_results: list[LyricsSearchCandidate] = []
        errors: list[str] = []
        for provider in allowed_providers:
            for query_variant in _build_query_variants(normalized_keyword):
                try:
                    all_results.extend(provider.search(query_variant, limit=limit))
                except LyricsSearchError as exc:
                    errors.append(str(exc))

        if not all_results and errors:
            unique_errors = list(dict.fromkeys(errors))
            raise LyricsSearchError("\n".join(unique_errors))

        ranked: dict[tuple[str, str, str, int], LyricsSearchCandidate] = {}
        for candidate in all_results:
            _rank_candidate(candidate, normalized_keyword)
            dedupe_key = (
                _normalize_text(candidate.title),
                _normalize_text(candidate.artist),
                _normalize_text(candidate.album),
                int(round(candidate.duration_seconds or 0.0)),
            )
            existing = ranked.get(dedupe_key)
            if existing is None or _sort_key(candidate) > _sort_key(existing):
                ranked[dedupe_key] = candidate

        ordered = sorted(ranked.values(), key=_sort_key, reverse=True)
        return ordered[:limit]


def build_lyrics_preview(candidate: LyricsSearchCandidate, preview_mode: str) -> LyricsPreview:
    parsed_synced_lines = parse_lrc_lines(candidate.synced_lyrics)
    if preview_mode == LYRICS_PREVIEW_VERBATIM:
        if parsed_synced_lines:
            return LyricsPreview(
                text=_build_estimated_verbatim_lrc(parsed_synced_lines),
                used_synced_lyrics=True,
                used_estimated_char_timing=True,
            )
        return LyricsPreview(
            text=candidate.plain_lyrics.strip() or candidate.synced_lyrics.strip(),
            used_synced_lyrics=False,
            used_estimated_char_timing=False,
        )

    if parsed_synced_lines:
        return LyricsPreview(
            text="\n".join(f"[{format_lrc_timestamp(line.start_ms)}]{line.text}" for line in parsed_synced_lines),
            used_synced_lyrics=True,
            used_estimated_char_timing=False,
        )

    return LyricsPreview(
        text=candidate.plain_lyrics.strip() or candidate.synced_lyrics.strip(),
        used_synced_lyrics=False,
        used_estimated_char_timing=False,
    )


def parse_lrc_lines(text: str) -> list[ParsedLrcLine]:
    lines: list[ParsedLrcLine] = []
    for raw_line in text.splitlines():
        matches = list(_TIMESTAMP_PATTERN.finditer(raw_line))
        if not matches:
            continue
        lyric_text = _TIMESTAMP_PATTERN.sub("", raw_line).strip()
        for match in matches:
            lines.append(ParsedLrcLine(start_ms=_parse_timestamp_match(match), text=lyric_text))
    return sorted(lines, key=lambda line: line.start_ms)


def format_lrc_timestamp(milliseconds: int) -> str:
    total_ms = max(0, milliseconds)
    minutes = total_ms // 60_000
    seconds = (total_ms % 60_000) // 1_000
    hundredths = (total_ms % 1_000) // 10
    return f"{minutes:02}:{seconds:02}.{hundredths:02}"


def _build_estimated_verbatim_lrc(lines: list[ParsedLrcLine]) -> str:
    rendered_lines: list[str] = []
    for index, line in enumerate(lines):
        if not line.text:
            continue
        next_start_ms = lines[index + 1].start_ms if index + 1 < len(lines) else None
        line_end_ms = _estimate_line_end_ms(line.start_ms, next_start_ms, line.text)
        rendered_lines.append(_render_verbatim_line(line.start_ms, line_end_ms, line.text))
    return "\n".join(rendered_lines)


def _render_verbatim_line(start_ms: int, end_ms: int, text: str) -> str:
    visible_count = sum(1 for char in text if not char.isspace())
    if visible_count <= 0:
        return f"[{format_lrc_timestamp(start_ms)}]{text}"

    span_ms = max(end_ms - start_ms, visible_count * 40)
    step_ms = max(span_ms // visible_count, 40)
    current_index = 0
    parts = [f"[{format_lrc_timestamp(start_ms)}]"]
    for char in text:
        if char.isspace():
            parts.append(char)
            continue
        char_start = start_ms + min(current_index * step_ms, span_ms)
        char_end = min(end_ms, char_start + step_ms)
        parts.append(f"<{format_lrc_timestamp(char_start)}>{char}<{format_lrc_timestamp(char_end)}>")
        current_index += 1
    return "".join(parts)


def _estimate_line_end_ms(start_ms: int, next_start_ms: int | None, text: str) -> int:
    if next_start_ms is not None and next_start_ms > start_ms:
        return next_start_ms
    visible_chars = max(1, sum(1 for char in text if not char.isspace()))
    estimated_span = min(max(visible_chars * 140, 1_200), 7_000)
    return start_ms + estimated_span


def _build_query_variants(keyword: str) -> list[str]:
    variants: list[str] = []

    def add(candidate: str) -> None:
        cleaned = " ".join(candidate.split())
        if cleaned and cleaned not in variants:
            variants.append(cleaned)

    add(keyword)
    for separator in (" - ", " / ", "／", "|"):
        if separator in keyword:
            parts = [part.strip() for part in keyword.split(separator) if part.strip()]
            if len(parts) >= 2:
                add(" ".join(parts))
                for part in parts:
                    add(part)
            break
    return variants


def _rank_candidate(candidate: LyricsSearchCandidate, keyword: str) -> None:
    candidate.title_score = _score_text(keyword, candidate.title)
    candidate.artist_score = _score_text(keyword, candidate.artist)
    candidate.album_score = _score_text(keyword, candidate.album)
    candidate.lyrics_score = _score_lyrics(keyword, candidate.plain_lyrics or candidate.synced_lyrics)
    candidate.display_score = round(
        candidate.title_score * 0.55
        + candidate.artist_score * 0.25
        + candidate.album_score * 0.12
        + candidate.lyrics_score * 0.08
        + (2.5 if candidate.has_synced_lyrics else 0.0),
        1,
    )
    candidate.match_source = _best_match_source(candidate)


def _best_match_source(candidate: LyricsSearchCandidate) -> str:
    field_scores = {
        "歌名": candidate.title_score,
        "歌手": candidate.artist_score,
        "专辑": candidate.album_score,
        "歌词片段": candidate.lyrics_score,
    }
    return max(field_scores.items(), key=lambda item: item[1])[0]


def _sort_key(candidate: LyricsSearchCandidate) -> tuple[float, float, float, float, int, float]:
    return (
        candidate.title_score,
        candidate.artist_score,
        candidate.album_score,
        candidate.lyrics_score,
        1 if candidate.has_synced_lyrics else 0,
        candidate.display_score,
    )


def _score_lyrics(keyword: str, lyrics_text: str) -> float:
    normalized_keyword = _normalize_text(keyword)
    normalized_lyrics = _normalize_text(lyrics_text)
    if not normalized_keyword or not normalized_lyrics:
        return 0.0
    if normalized_keyword in normalized_lyrics:
        return 100.0

    best_score = 0.0
    for line in lyrics_text.splitlines():
        if not line.strip():
            continue
        best_score = max(best_score, _score_text(keyword, line))
        if best_score >= 96:
            break
    return best_score


def _score_text(keyword: str, text: str) -> float:
    normalized_keyword = _normalize_text(keyword)
    normalized_text = _normalize_text(text)
    if not normalized_keyword or not normalized_text:
        return 0.0
    if normalized_keyword == normalized_text:
        return 100.0

    score = SequenceMatcher(None, normalized_keyword, normalized_text).ratio() * 100
    if normalized_keyword in normalized_text:
        score = max(score, 92.0)

    keyword_tokens = _tokenize(normalized_keyword)
    text_tokens = _tokenize(normalized_text)
    if keyword_tokens and text_tokens:
        overlap = sum(1 for token in keyword_tokens if token in text_tokens)
        coverage = overlap / len(keyword_tokens)
        if coverage:
            score = max(score, coverage * 95.0)
        prefix_hits = sum(1 for token in keyword_tokens if any(text_token.startswith(token) for text_token in text_tokens))
        if prefix_hits:
            score = max(score, prefix_hits / len(keyword_tokens) * 90.0)

    return round(min(score, 100.0), 2)


def _tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_SPLIT_PATTERN.split(text) if token]


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _parse_timestamp_match(match: re.Match[str]) -> int:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3) or "00"
    milliseconds = int(fraction.ljust(3, "0")[:3])
    return minutes * 60_000 + seconds * 1_000 + milliseconds


def _coerce_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
