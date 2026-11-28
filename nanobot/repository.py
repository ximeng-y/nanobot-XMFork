"""Canonical GitHub repository for this personal fork."""

OWNER = "ximeng-y"
NAME = "nanobot-XMFork"
HTML_URL = f"https://github.com/{OWNER}/{NAME}"
SOURCE_URL = f"{HTML_URL}.git"
TAGS_URL = f"https://api.github.com/repos/{OWNER}/{NAME}/tags?per_page=100"


def release_url(tag: str) -> str:
    return f"{HTML_URL}/releases/tag/{tag}"
