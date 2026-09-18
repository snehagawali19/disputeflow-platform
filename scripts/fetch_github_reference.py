"""Fetch the configured GitHub repository as a read-only project reference.

The token is read from GITHUB_TOKEN and is never printed, persisted, or passed
in the repository URL. Existing references are protected unless --refresh is
explicitly supplied.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = PROJECT_ROOT / "_reference" / "dispute-automation"


def repository_api_url(repository_url: str) -> str:
    parsed = urlparse(repository_url.rstrip("/"))
    if parsed.netloc.lower() != "github.com":
        raise ValueError("GITHUB_REPO_URL must point to github.com")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("GITHUB_REPO_URL must have the form https://github.com/owner/repository")
    owner, repository = parts
    repository = re.sub(r"\.git$", "", repository)
    return f"https://api.github.com/repos/{owner}/{repository}/zipball"


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> int:
    count = 0
    destination = destination.resolve()
    for member in archive.infolist():
        relative = PurePosixPath(member.filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe path in GitHub archive: {member.filename}")
        target = (destination / Path(*relative.parts)).resolve()
        if destination not in target.parents and target != destination:
            raise ValueError(f"Unsafe extraction target: {member.filename}")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        count += 1
    return count


def fetch(repository_url: str, token: str, destination: Path, refresh: bool) -> int:
    if destination.exists():
        if not refresh:
            raise FileExistsError(f"Reference already exists: {destination}. Use --refresh to replace it.")
        shutil.rmtree(destination)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "DisputeFlow-reference-fetcher",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        response = client.get(repository_api_url(repository_url), headers=headers)
        response.raise_for_status()

    staging_parent = Path(tempfile.mkdtemp(prefix="disputeflow-github-", dir=PROJECT_ROOT))
    staging = staging_parent / "repository"
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            file_count = safe_extract(archive, staging)
        roots = [path for path in staging.iterdir() if path.is_dir()]
        source_root = roots[0] if len(roots) == 1 else staging
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_root), str(destination))
        return file_count
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Replace an existing reference")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    repository_url = os.getenv("GITHUB_REPO_URL", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not repository_url:
        raise SystemExit("GITHUB_REPO_URL is not configured")
    try:
        count = fetch(repository_url, token, args.destination.resolve(), args.refresh)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SystemExit(f"GitHub reference fetch failed: {exc}") from exc
    print(f"Fetched GitHub reference: {repository_url}")
    print(f"Files imported: {count}")
    print(f"Destination: {args.destination.resolve()}")


if __name__ == "__main__":
    main()
