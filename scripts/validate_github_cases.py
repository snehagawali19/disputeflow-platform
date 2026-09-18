"""Validate local GitHub dispute JSON files, artifact paths, and SHA-256 hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/").lstrip("/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"Unsafe artifact path: {path}")
    if not normalized.startswith("disputes/artifacts/"):
        raise ValueError(f"Artifact must live under disputes/artifacts/: {path}")
    return normalized


def main() -> int:
    input_dir = ROOT / "disputes" / "input"
    files = sorted(input_dir.glob("dispute-*.json"))
    if len(files) != 20:
        print(f"expected 20 files, found {len(files)}")
        return 1
    errors = 0
    ids = set()
    required = ("dispute_id", "reason_code", "dispute_phase", "dispute_amount", "artifacts")
    for path in files:
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"{path.name}: invalid JSON ({exc})")
            errors += 1
            continue
        if not isinstance(payload, dict):
            print(f"{path.name}: JSON root must be an object")
            errors += 1
            continue
        for field in required:
            if field not in payload:
                print(f"{path.name}: missing {field}")
                errors += 1
        dispute_id = payload.get("dispute_id")
        if dispute_id in ids:
            print(f"{path.name}: duplicate dispute_id")
            errors += 1
        ids.add(dispute_id)
        artifacts = payload.get("artifacts") or []
        if not artifacts:
            print(f"{path.name}: no artifacts")
            errors += 1
        for artifact in artifacts:
            try:
                rel = safe_repo_path(str(artifact.get("repository_path") or ""))
            except ValueError as exc:
                print(f"{path.name}: {exc}")
                errors += 1
                continue
            artifact_path = ROOT / rel
            if not artifact_path.is_file():
                print(f"{path.name}: missing {rel}")
                errors += 1
                continue
            digest = sha256_hex(artifact_path.read_bytes())
            if digest.lower() != str(artifact.get("sha256") or "").lower():
                print(f"{path.name}: hash mismatch for {rel}")
                errors += 1
        email = str((payload.get("customer") or {}).get("email") or "")
        if email and not email.endswith(".test"):
            print(f"{path.name}: email must use .test")
            errors += 1
    if errors:
        print(f"validation failed with {errors} error(s)")
        return 1
    print("ok: 20 disputes, artifacts present, hashes match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
