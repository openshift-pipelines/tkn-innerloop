"""Container runtime detection and image inspection."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def resolve_runtime(requested: str) -> str:
    """auto prefers podman (rootless: files stay yours), then docker."""
    if requested != "auto":
        return requested
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    raise ValueError(
        "No container runtime found (install podman or docker, or use --runtime host to run the scripts directly)"
    )


def hand_back_ownership(runtime: str, root: Path, step: dict, results: Path, image: str) -> None:
    """Docker keeps the container's uid, so a step can leave root-owned files in the checkout.

    Give them back to the invoking user, using the step's own image as root. Rootless
    podman maps root to the user already, and failure here must never fail the check.
    """
    uid, gid = os.getuid(), os.getgid()
    if runtime != "docker" or uid == 0:
        return
    try:
        subprocess.run(
            [
                runtime, "run", "--rm", "--user", "0", "-v", f"{root}:/workspace:Z",
                "-v", f"{results}:/results:Z",
                "--entrypoint", step["shell"], image, "-c",
                f"find /workspace /results ! -user {uid} -exec chown {uid}:{gid} {{}} +",
            ],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def local_image_id(runtime: str, image: str) -> str:
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", "--format", "{{.Id}}", image],
            check=False, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    current = result.stdout.strip() if isinstance(result.stdout, str) else ""
    return current if not result.returncode else ""


def cache_image_id(runtime: str, image: str, timeout: float) -> str:
    current = local_image_id(runtime, image)
    if not current:
        subprocess.run([runtime, "pull", image], check=True, timeout=timeout)
        current = local_image_id(runtime, image)
    if not current:
        raise ValueError(f"{runtime} could not determine the image ID for {image}")
    return current


def cache_image_uid(runtime: str, image_id: str, timeout: float) -> int:
    result = subprocess.run(
        [runtime, "image", "inspect", "--format", "{{.Config.User}}", image_id],
        capture_output=True, text=True, timeout=timeout, check=True,
    )
    user = result.stdout.strip().partition(":")[0]
    if user and not user.isdecimal():
        result = subprocess.run(
            [runtime, "run", "--rm", "--network", "none", "--entrypoint", "id", image_id, "-u"],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        user = result.stdout.strip()
    return int(user or "0")
