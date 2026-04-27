#!/usr/bin/env python3
"""
scripts/cleanup.py
Artifex360 cleanup utility -- removes conversations, logs, caches, and
__pycache__ directories.

Usage:
    python scripts/cleanup.py           # Interactive (confirms before deleting)
    python scripts/cleanup.py --yes     # Skip confirmation
    python scripts/cleanup.py --dry-run # Show what would be deleted
"""

import argparse
import glob
import os
import shutil
import sys

# Project root is one level up from scripts/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_targets():
    """Return a dict of category -> list of paths to remove."""
    targets = {}

    # 1. Conversations
    convos = glob.glob(os.path.join(PROJECT_ROOT, "data", "conversations", "*.json"))
    if convos:
        targets["Conversations"] = convos

    # 2. Failure reports
    reports = glob.glob(os.path.join(PROJECT_ROOT, "data", "conversations", "*_failure_report.*"))
    if reports:
        targets["Failure reports"] = reports

    # 3. Log files
    logs = glob.glob(os.path.join(PROJECT_ROOT, "*.log"))
    logs += glob.glob(os.path.join(PROJECT_ROOT, "logs", "*.log"))
    logs += glob.glob(os.path.join(PROJECT_ROOT, "data", "*.log"))
    if logs:
        targets["Log files"] = logs

    # 4. __pycache__ directories
    pycache = []
    for root, dirs, _files in os.walk(PROJECT_ROOT):
        # Skip .git and node_modules
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "venv")]
        for d in dirs:
            if d == "__pycache__":
                pycache.append(os.path.join(root, d))
    if pycache:
        targets["__pycache__ directories"] = pycache

    # 5. .pyc files (loose ones outside __pycache__)
    pyc = glob.glob(os.path.join(PROJECT_ROOT, "**", "*.pyc"), recursive=True)
    pyc = [f for f in pyc if "__pycache__" not in f]
    if pyc:
        targets["Loose .pyc files"] = pyc

    # 6. pytest cache
    pytest_cache = os.path.join(PROJECT_ROOT, ".pytest_cache")
    if os.path.isdir(pytest_cache):
        targets["pytest cache"] = [pytest_cache]

    # 7. Ollama model cache (can be regenerated)
    ollama_cache = os.path.join(PROJECT_ROOT, "data", "ollama_models_cache.json")
    if os.path.exists(ollama_cache):
        targets["Ollama model cache"] = [ollama_cache]

    # 8. PID lock file
    pid_file = os.path.join(PROJECT_ROOT, "data", ".artifex360.pid")
    if os.path.exists(pid_file):
        targets["PID lock file"] = [pid_file]

    # 9. Uploaded files (user uploads)
    uploads = []
    upload_dir = os.path.join(PROJECT_ROOT, "data", "uploads")
    if os.path.isdir(upload_dir):
        for f in os.listdir(upload_dir):
            if f == ".gitkeep" or os.path.isdir(os.path.join(upload_dir, f)):
                continue
            uploads.append(os.path.join(upload_dir, f))
    if uploads:
        targets["Uploaded files"] = uploads

    return targets


def print_targets(targets):
    """Display what will be cleaned."""
    total = 0
    for category, paths in targets.items():
        count = len(paths)
        total += count
        print(f"\n  [{count:>3}] {category}")
        for p in paths[:5]:
            rel = os.path.relpath(p, PROJECT_ROOT)
            print(f"        {rel}")
        if count > 5:
            print(f"        ... and {count - 5} more")
    print(f"\n  Total: {total} items")
    return total


def remove_targets(targets, dry_run=False):
    """Delete all targets. Returns count of items removed."""
    removed = 0
    for category, paths in targets.items():
        for p in paths:
            rel = os.path.relpath(p, PROJECT_ROOT)
            if dry_run:
                print(f"  [DRY RUN] Would remove: {rel}")
                removed += 1
                continue
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                removed += 1
            except OSError as exc:
                print(f"  [ERROR] Could not remove {rel}: {exc}")
    return removed


def main():
    parser = argparse.ArgumentParser(
        description="Artifex360 cleanup -- remove conversations, logs, caches",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be deleted without removing anything",
    )
    args = parser.parse_args()

    print("Artifex360 Cleanup")
    print("=" * 40)

    targets = find_targets()

    if not targets:
        print("\n  Nothing to clean. All clear.")
        return

    total = print_targets(targets)

    if args.dry_run:
        print()
        remove_targets(targets, dry_run=True)
        print(f"\n  [DRY RUN] Would remove {total} items.")
        return

    if not args.yes:
        print()
        answer = input("  Proceed with cleanup? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Cancelled.")
            return

    removed = remove_targets(targets)
    print(f"\n  Cleaned up {removed} items.")


if __name__ == "__main__":
    main()
