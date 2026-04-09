import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath


_PATH_SPLIT_PATTERN = re.compile(r"[\s,，;；:：\n\r\t]+")
_WRAPPER_CHARS = "\"'`<>{}[]()（）"
_ACTION_PHRASE_PATTERN = re.compile(
    r"(?:^|[_\-/])(commit|push|pull-?request|pr|merge)(?:$|[_\-/])|提交|变更提交",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class ExpectedOutputPathResolution:
    normalized_path: str
    raw_value: str
    used_default: bool


class ExpectedOutputPathError(ValueError):
    def __init__(self, raw_value: str | None, message: str, suggestion: str | None = None):
        self.raw_value = raw_value or ""
        self.suggestion = suggestion
        detail = message
        if suggestion:
            detail = f"{detail} Suggested format: {suggestion}"
        super().__init__(detail)


def safe_join(base: str, relative: str) -> str:
    """Safely join base and relative paths, preventing directory traversal."""
    joined = os.path.realpath(os.path.join(base, relative))
    base_real = os.path.realpath(base)
    if not (joined == base_real or joined.startswith(base_real + os.sep)):
        raise PermissionError(f"Path traversal detected: '{relative}' escapes base directory")
    return joined


def _normalize_text(value: str | None) -> str:
    return unicodedata.normalize("NFKC", (value or "")).strip()


def _strip_wrappers(value: str) -> str:
    return value.strip().strip(_WRAPPER_CHARS)


def _split_candidates(value: str) -> list[str]:
    parts = [_strip_wrappers(part) for part in _PATH_SPLIT_PATTERN.split(value)]
    return [part for part in parts if part]


def _looks_like_action_phrase(path: str) -> bool:
    name = PurePosixPath(path.rstrip("/")).name
    if not name or "." in name:
        return False
    return bool(_ACTION_PHRASE_PATTERN.search(name))


def _normalize_candidate_path(candidate: str) -> str:
    normalized = candidate.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/{2,}", "/", normalized)
    normalized = normalized.rstrip("/")
    parts: list[str] = []
    for part in normalized.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ExpectedOutputPathError(candidate, "expected_output must stay within the repository root")
        parts.append(part)
    normalized = "/".join(parts)
    if not normalized:
        raise ExpectedOutputPathError(candidate, "expected_output cannot be empty after normalization")
    return normalized


def _apply_collaboration_dir(candidate: str, collaboration_dir: str) -> str:
    collab = _normalize_candidate_path(collaboration_dir) if collaboration_dir else ""
    normalized = candidate
    if collab.startswith("outputs/") and normalized.startswith("outputs/") and not normalized.startswith(collab + "/"):
        normalized = normalized[len("outputs/"):]
    if collab and normalized != collab and not normalized.startswith(collab + "/"):
        return f"{collab}/{normalized}"
    return normalized


def _pick_candidate(raw_value: str, collaboration_dir: str) -> str:
    candidates = _split_candidates(raw_value)
    collab = _normalize_text(collaboration_dir).strip("/")
    for token in candidates:
        if "/" in token or token.startswith("./") or token.startswith(".\\"):
            return token
        if collab and token.startswith(collab):
            return token
        if "." in PurePosixPath(token).name:
            return token
    if candidates:
        return candidates[0]
    return raw_value


def _build_suggestion(default_path: str, collaboration_dir: str) -> str:
    suggested = _apply_collaboration_dir(_normalize_candidate_path(default_path), collaboration_dir)
    parent = str(PurePosixPath(suggested).parent)
    stem = PurePosixPath(suggested).stem
    return f"{suggested} or {parent}/{stem}.md"


def resolve_expected_output_path(
    raw_value: str | None,
    default_path: str,
    collaboration_dir: str = "",
    *,
    strict: bool = True,
) -> ExpectedOutputPathResolution:
    text = _normalize_text(raw_value)
    default_normalized = _apply_collaboration_dir(_normalize_candidate_path(default_path), collaboration_dir)
    if not text:
        return ExpectedOutputPathResolution(
            normalized_path=default_normalized,
            raw_value="",
            used_default=True,
        )

    if text.startswith(("/", "\\")) or _WINDOWS_DRIVE_PATTERN.match(text):
        raise ExpectedOutputPathError(
            text,
            "expected_output must be a repository-relative path, not an absolute path",
            _build_suggestion(default_path, collaboration_dir),
        )

    candidate = _pick_candidate(text, collaboration_dir)
    normalized = _apply_collaboration_dir(_normalize_candidate_path(candidate), collaboration_dir)
    if strict and _looks_like_action_phrase(normalized):
        raise ExpectedOutputPathError(
            text,
            "expected_output looks like an action phrase, not a file path. Use a repo-relative output path instead of phrases like '代码变更提交' or '提交PR'.",
            _build_suggestion(default_path, collaboration_dir),
        )

    return ExpectedOutputPathResolution(
        normalized_path=normalized,
        raw_value=text,
        used_default=False,
    )


def extract_json_path(value: str | None) -> str:
    if not _normalize_text(value):
        return ""
    return resolve_expected_output_path(
        value,
        default_path="result.json",
        collaboration_dir="",
        strict=False,
    ).normalized_path


def normalize_expected_output_path(
    raw_value: str | None,
    default_path: str,
    collaboration_dir: str = "",
    *,
    strict: bool = True,
) -> str:
    return resolve_expected_output_path(
        raw_value,
        default_path=default_path,
        collaboration_dir=collaboration_dir,
        strict=strict,
    ).normalized_path
