#!/usr/bin/env python3
"""Export a curated source archive without local artifacts or Git history."""

import argparse
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ".gitignore", ".dockerignore", "README.md", "start.sh",
    "docker-compose.yml", "docker-compose.data.yml",
    "backend/Dockerfile", "backend/requirements.txt", "backend/requirements-dev.txt",
    "frontend/Dockerfile", "frontend/.env.example", "frontend/index.html",
    "frontend/nginx.conf", "frontend/package.json", "frontend/package-lock.json",
    "frontend/tsconfig.json", "frontend/vite.config.ts",
)
SOURCE_GROUPS = (
    ("backend/app", {".py"}),
    ("backend/tests", {".py"}),
    ("frontend/src", {".ts", ".tsx", ".css"}),
    ("frontend/tests", {".mjs"}),
    ("demo_data", {".csv", ".md"}),
    ("docs", {".md", ".png", ".webp"}),
    ("scripts", {".py"}),
    (".github/workflows", {".yml", ".yaml"}),
)
CHECKS = {
    "credential": re.compile(
        rb"(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
        rb"github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
        rb"sk-[A-Za-z0-9_-]{20,}"
    ),
    "personal filesystem path": re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    "personal email": re.compile(rb"[\w.+-]+@(?:gmail|qq|outlook|hotmail)\.[\w.-]+", re.IGNORECASE),
}


def public_files() -> list[Path]:
    paths = {ROOT / name for name in REQUIRED_FILES}
    # A chosen license can be included without assigning one during export.
    paths.update(path for path in ROOT.glob("LICENSE*") if path.is_file())
    for directory, extensions in SOURCE_GROUPS:
        paths.update(path for path in (ROOT / directory).rglob("*") if path.is_file() and path.suffix in extensions)
    for path in paths:
        relative = path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Missing file or unsafe source path: {relative}")
        if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(ROOT)):
            raise ValueError(f"Symlinked source directory: {relative}")
    return sorted(paths)


def export(output: Path) -> int:
    paths = public_files()
    # Read once: the bytes checked below are the bytes written to the archive.
    contents = [(path.relative_to(ROOT), path.read_bytes(), path.stat().st_mode) for path in paths]
    for relative, content, _ in contents:
        if relative.suffix in {".png", ".webp"}:
            continue
        for label, pattern in CHECKS.items():
            if pattern.search(content):
                # Report the filename and type only, never the matching value.
                raise ValueError(f"Review {relative}: possible {label}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, content, mode in contents:
            info = zipfile.ZipInfo(f"satellite-visual-simulator/{relative.as_posix()}", date_time=(2025, 1, 1, 0, 0, 0))
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return len(contents)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "public-release" / "satellite-visual-simulator.zip")
    args = parser.parse_args()
    count = export(args.output)
    print(f"Exported {count} files to {args.output}; no Git history or local datasets included.")
