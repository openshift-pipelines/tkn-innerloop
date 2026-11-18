"""Fetching Tekton bundle objects with skopeo."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

from .paths import cache_home
from .ui import style

BUNDLE_DIR_ENV = "TKN_INNERLOOP_BUNDLE_DIR"
BUNDLE_KINDS = {"task": "Task", "pipeline": "Pipeline", "stepaction": "StepAction"}
BUNDLE_INPUT = re.compile(
    r"bundle:(?P<repo>[^@#\s]+)@(?P<digest>sha256:[0-9a-f]{64})#(?P<kind>[a-z]+)/(?P<name>[^/\s]+)"
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class BundleError(ValueError):
    """A bundles-resolver reference that could not be fetched or read."""


_bundle_digests: dict[str, str] = {}
_bundle_failures: dict[str, BundleError] = {}


def skopeo(*args: str, timeout: float = 120) -> str:
    tool = shutil.which("skopeo")
    if not tool:
        raise BundleError("install skopeo to fetch Tekton bundles")
    try:
        result = subprocess.run(
            [tool, *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"skopeo failed: {exc}") from None
    if result.returncode:
        reason = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise BundleError(f"skopeo failed: {reason}")
    return result.stdout


def split_image(image: str) -> tuple[str, str | None, str | None]:
    """Repository, tag and digest of an image reference such as repo:tag@sha256:..."""
    base, _, digest = image.partition("@")
    slash, colon = base.rfind("/"), base.rfind(":")
    if colon > slash:
        return base[:colon], base[colon + 1 :], digest or None
    return base, None, digest or None


def bundle_input(image: str, kind: str, name: str) -> str:
    """The plan input name of a bundle object, pinned to a digest.

    skopeo rejects references with both a tag and a digest, so the digest alone is kept.
    """
    repo, tag, digest = split_image(image.strip())
    if digest is None:
        reference = f"{repo}:{tag or 'latest'}"
        if reference not in _bundle_digests:
            _bundle_digests[reference] = skopeo(
                "inspect", "--format", "{{.Digest}}", f"docker://{reference}"
            ).strip()
        digest = _bundle_digests[reference]
    if not DIGEST.fullmatch(digest):
        raise BundleError(f"unsupported bundle digest {digest}")
    return f"bundle:{repo}@{digest}#{kind}/{name}"


def bundle_location(base: Path, repo: str, digest: str, kind: str, name: str) -> Path:
    def safe(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)

    return base.joinpath(*map(safe, repo.split("/")), digest.split(":")[1], f"{kind}-{safe(name)}.yaml")


def bundle_path(input_name: str) -> Path:
    """The local copy of a bundle object: from TKN_INNERLOOP_BUNDLE_DIR, the cache, or the registry.

    Objects are pinned to a digest, so a cached copy never goes stale.
    """
    match = BUNDLE_INPUT.fullmatch(input_name)
    if not match:
        raise ValueError(f"Malformed bundle input: {input_name}")
    parts = match.group("repo", "digest", "kind", "name")
    mirror = os.environ.get(BUNDLE_DIR_ENV)
    if mirror and (path := bundle_location(Path(mirror), *parts)).is_file():
        return path
    path = bundle_location(cache_home() / "tkn-innerloop" / "bundles", *parts)
    if path.is_file():
        return path
    if input_name in _bundle_failures:
        raise _bundle_failures[input_name]
    try:
        data = download_bundle(*parts)
    except BundleError as exc:
        _bundle_failures[input_name] = exc
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}")
    partial.write_bytes(data)
    partial.replace(path)
    return path


def download_bundle(repo: str, digest: str, kind: str, name: str) -> bytes:
    """The raw object in the bundle layer annotated with its kind and name."""
    print(
        style(f"  … Bundle    fetching {repo.rsplit('/', 1)[-1]} ({kind} {name})", "2", sys.stderr),
        file=sys.stderr,
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="tkn-innerloop-bundle-") as tmp:
        skopeo("copy", "-q", f"docker://{repo}@{digest}", f"dir:{tmp}")
        try:
            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise BundleError(f"cannot read the bundle manifest: {exc}") from None
        layers = [
            layer
            for layer in manifest.get("layers") or []
            if str((layer.get("annotations") or {}).get("dev.tekton.image.kind", "")).lower() == kind
            and (layer.get("annotations") or {}).get("dev.tekton.image.name") == name
        ]
        if not layers:
            raise BundleError(f"the bundle has no {kind} named {name}")
        blob = Path(tmp) / str(layers[0].get("digest", "")).split(":")[-1]
        try:
            with tarfile.open(blob) as tar:
                members = [member for member in tar.getmembers() if member.isfile()]
                if len(members) != 1:
                    raise BundleError(f"the {kind} {name} layer should hold one file")
                return tar.extractfile(members[0]).read()
        except (OSError, tarfile.TarError) as exc:
            raise BundleError(f"cannot read the {kind} {name} layer: {exc}") from None


def bundle_definition(ref: object, kind: str) -> tuple[dict, str] | None:
    """The object a bundles-resolver reference points to and its plan input name, else None.

    Raises BundleError when it is a bundle reference that cannot be fetched or read.
    """
    if not isinstance(ref, dict) or ref.get("resolver") != "bundles":
        return None
    params = {
        p.get("name"): p.get("value") for p in ref.get("params") or [] if isinstance(p, dict)
    }
    image, name, wanted = params.get("bundle"), params.get("name"), params.get("kind", kind)
    if not all(isinstance(v, str) and v and "$(" not in v for v in (image, name, wanted)):
        raise BundleError("bundle, name and kind must be literal values")
    if wanted.lower() != kind.lower():
        raise BundleError(f"expected a {kind} bundle, not {wanted}")
    input_name = bundle_input(image, kind.lower(), name)
    try:
        document = yaml.safe_load(bundle_path(input_name).read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BundleError(f"cannot read the {kind} {name}: {exc}") from None
    if not isinstance(document, dict) or document.get("kind") != kind:
        raise BundleError(f"the bundle object {name} is not a {kind}")
    return document, input_name


def input_label(name: str) -> str:
    match = BUNDLE_INPUT.fullmatch(name)
    return f"bundle {match['repo'].rsplit('/', 1)[-1]}" if match else name
