"""Load source documents and their access metadata."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PyPdfError
import yaml


GROUPS = ("all", "hr", "finance", "sales", "exec")
SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}
REQUIRED_FRONT_MATTER = ("title", "version", "effective_date", "doc_status")


@dataclass(frozen=True)
class SourceDocument:
    source: str
    text: str
    metadata: dict[str, str]
    allowed_groups: frozenset[str]


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8")


def _parse_front_matter(source: str, text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{source}: missing YAML front matter")

    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ValueError(f"{source}: unterminated YAML front matter") from error

    try:
        values: Any = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise ValueError(f"{source}: invalid YAML front matter: {error}") from error
    if not isinstance(values, dict):
        raise ValueError(f"{source}: YAML front matter must be a mapping")

    missing = [key for key in REQUIRED_FRONT_MATTER if key not in values]
    if missing:
        raise ValueError(f"{source}: missing front-matter fields: {', '.join(missing)}")

    metadata: dict[str, str] = {}
    for key in ("title", "version", "doc_status"):
        value = values[key]
        if not isinstance(value, (str, int, float)) or not str(value).strip():
            raise ValueError(f"{source}: front-matter field {key!r} must be a non-empty scalar")
        metadata[key] = str(value)

    effective_date = values["effective_date"]
    if isinstance(effective_date, datetime):
        metadata["effective_date"] = effective_date.date().isoformat()
    elif isinstance(effective_date, date):
        metadata["effective_date"] = effective_date.isoformat()
    elif isinstance(effective_date, str) and effective_date.strip():
        metadata["effective_date"] = effective_date.strip()
    else:
        raise ValueError(f"{source}: front-matter field 'effective_date' must be a date or string")

    body = "\n".join(lines[closing + 1:]).strip()
    if not body:
        raise ValueError(f"{source}: document body is empty")
    return metadata, body


def load_documents(data_dir: Path) -> list[SourceDocument]:
    access_path = data_dir / "access.json"
    access_data = json.loads(access_path.read_text(encoding="utf-8"))
    access_entries = access_data.get("documents")
    if not isinstance(access_entries, dict):
        raise ValueError(f"{access_path}: missing documents mapping")

    documents = []
    errors: list[str] = []
    for path in sorted(data_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            documents.append(_load_one(path, access_entries))
        except ValueError as error:
            errors.append(str(error))
        except (OSError, PyPdfError) as error:
            errors.append(f"{path.name}: could not read file ({type(error).__name__})")

    # Fail closed: report every bad file at once, index nothing.
    if errors:
        listing = "\n".join(f"  - {message}" for message in errors)
        raise ValueError(f"{len(errors)} document(s) failed validation:\n{listing}")
    if not documents:
        raise ValueError(f"No supported documents found in {data_dir}")
    return documents


def _load_one(path: Path, access_entries: dict[str, Any]) -> SourceDocument:
    permission = access_entries.get(path.name)
    if not isinstance(permission, dict):
        raise ValueError(f"{path.name}: missing access.json document entry")
    groups = permission.get("allowed_groups")
    if not isinstance(groups, list) or any(not isinstance(group, str) for group in groups):
        raise ValueError(f"{path.name}: allowed_groups must be a list of group names in access.json")
    allowed_groups = frozenset(groups)
    unknown_groups = allowed_groups.difference(GROUPS)
    if unknown_groups:
        raise ValueError(f"{path.name}: unknown access groups: {', '.join(sorted(unknown_groups))}")

    metadata, body = _parse_front_matter(path.name, _read_text(path))
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    metadata["last_modified"] = modified
    return SourceDocument(path.name, body, metadata, allowed_groups)