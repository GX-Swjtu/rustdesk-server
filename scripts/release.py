"""Package the fork server without registry credentials or production data."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'^version = "([^"]+)"', (ROOT / "Cargo.toml").read_text(), re.M)[1]
REPO = "GX-Swjtu/rustdesk-server"
TARGETS = ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc")
DIST = ROOT / "dist/release"


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def filename(target):
    if target not in TARGETS:
        raise ValueError("Unsupported server target: " + target)
    return f"rustdesk-server-{VERSION}-{target}" + (".zip" if "windows" in target else ".tar.gz")


def package():
    target = os.environ["RELEASE_TARGET"]
    output = DIST / filename(target)
    extension = ".exe" if "windows" in target else ""
    files = [(ROOT / "target/release" / (name + extension), name + extension)
             for name in ("hbbs", "hbbr", "rustdesk-utils")]
    files += [(ROOT / name, name) for name in (
        "LICENSE", "README.md", "docs/native-p2p.md", "docs/account-handshake.md",
        "docs/environment-variables.md", "docs/fork-release.md")]
    source = json.dumps({"repository": REPO, "version": VERSION,
        "commit": command("git", "rev-parse", "HEAD"),
        "hbb_common": command("git", "rev-parse", "HEAD:libs/hbb_common")}, indent=2).encode()
    DIST.mkdir(parents=True, exist_ok=True)
    if extension:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, name in files:
                archive.write(path, name)
            archive.writestr("SOURCE.json", source)
    else:
        with tarfile.open(output, "w:gz") as archive:
            for path, name in files:
                archive.add(path, arcname=name)
            info = tarfile.TarInfo("SOURCE.json")
            info.size = len(source)
            archive.addfile(info, io.BytesIO(source))


def validate():
    tag = os.environ["RELEASE_TAG"]
    if not re.fullmatch(r"server-v" + re.escape(VERSION) + r"-gx\.[1-9]\d*", tag):
        raise ValueError(f"Use a new server-v{VERSION}-gx.REVISION tag")
    if command("git", "rev-parse", tag + "^{commit}") != command("git", "rev-parse", "HEAD"):
        raise ValueError("Select the immutable tag as the workflow ref")
    pages = json.loads(command("gh", "api", f"repos/{REPO}/releases", "--paginate", "--slurp"))
    existing = next((r for page in pages for r in page if r["tag_name"] == tag), None)
    if existing and not existing["draft"]:
        raise ValueError("Use a new revision; published releases are immutable")
    return tag, existing


def draft():
    tag, existing = validate()
    files = [DIST / filename(target) for target in TARGETS]
    if any(not p.is_file() or p.stat().st_size == 0 for p in files):
        raise ValueError("All three server packages must be present and nonempty")
    sums = DIST / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files), encoding="utf-8")
    body = DIST / "release-notes.md"
    warning = (ROOT / ".github/RELEASE_WARNING.md").read_text(encoding="utf-8")
    body.write_text(warning + f"\n# RustDesk GX server {tag}\n\n"
        + "包含 hbbs、hbbr、rustdesk-utils；不会自动部署。Includes hbbs, hbbr and rustdesk-utils; no automatic deployment.\n\n"
        + "Linux 包需要 Ubuntu 24.04 或相当系统；Windows 未签名。Linux requires Ubuntu 24.04 or equivalent; Windows binaries are unsigned.\n\n"
        + f"Source: `{command('git', 'rev-parse', 'HEAD')}`. See docs/fork-release.md and verify SHA256SUMS.\n", encoding="utf-8")
    if existing:
        command("gh", "release", "edit", tag, "--repo", REPO, "--draft", "--notes-file", str(body))
    else:
        command("gh", "release", "create", tag, "--repo", REPO, "--verify-tag", "--draft", "--title", tag, "--notes-file", str(body))
    command("gh", "release", "upload", tag, *map(str, files + [sums]), "--repo", REPO, "--clobber")


if __name__ == "__main__":
    import sys
    {"package": package, "validate": validate, "draft": draft}[sys.argv[1]]()
