import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parent.parent


def run(arguments: list[str], **options) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True, **options)


def build(skip_build: bool) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    operating_system = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform)
    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if operating_system is None or architecture is None or (operating_system != "macos" and architecture != "x86_64"):
        raise RuntimeError("Build on Windows/Linux x64, or macOS Intel/Apple Silicon.")
    stem = f"8t-daw-{version}-preview-{operating_system}-{architecture}"
    distribution = ROOT / "dist" / "desktop"
    releases = ROOT / "dist" / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)
    if not skip_build:
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/8t.spec",
             "--distpath", str(distribution), "--workpath", str(ROOT / "build" / "desktop")])
    executable = distribution / "8t" / ("8t.exe" if operating_system == "windows" else "8t")
    if operating_system == "macos":
        executable = distribution / "8T.app" / "Contents" / "MacOS" / "8t"
    report = releases / f"{stem}-smoke.json"
    report.unlink(missing_ok=True)
    run([str(executable), "--smoke-test", str(report)], timeout=120)
    if not report.is_file() or json.loads(report.read_text(encoding="utf-8")).get("status") != "passed":
        raise RuntimeError("The frozen application did not pass its smoke test.")
    if operating_system == "windows":
        compiler = shutil.which("ISCC") or str(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe")
        run([compiler, f"/DAppVersion={version}", f"/DSourceDir={distribution / '8t'}",
             f"/DReleaseDir={releases}", f"/DReleaseName={stem}", "packaging/windows.iss"])
        artifact = releases / f"{stem}.exe"
    elif operating_system == "macos":
        artifact = releases / f"{stem}.dmg"
        with tempfile.TemporaryDirectory(prefix="8t-dmg-", dir=ROOT / "build") as temporary:
            staging = Path(temporary)
            shutil.copytree(distribution / "8T.app", staging / "8T.app", symlinks=True)
            (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
            run(["hdiutil", "create", "-volname", "8T Preview", "-srcfolder", str(staging),
                 "-ov", "-format", "UDZO", str(artifact)])
    else:
        artifact = releases / f"{stem}.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            archive.add(distribution / "8t", arcname="8t")
    with artifact.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    (releases / f"{stem}.sha256").write_text(f"{checksum}  {artifact.name}\n", encoding="utf-8")
    packages = json.loads(subprocess.check_output([sys.executable, "-m", "pip", "list", "--format=json"], cwd=ROOT))
    manifest = {"version": version, "channel": "unsigned-preview", "platform": operating_system,
                "architecture": architecture, "filename": artifact.name, "sha256": checksum,
                "bytes": artifact.stat().st_size, "python": platform.python_version(),
                "build_os": platform.platform(), "build_libc": platform.libc_ver(),
                "signing_verified": False, "redistribution_review_required": True,
                "hardware_recording_tested": False, "packages": packages}
    (releases / f"{stem}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Preview artifact: {artifact}")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a native, unsigned 8T preview for this OS/architecture.")
    parser.add_argument("--skip-build", action="store_true", help="Package an existing bundle after rerunning its smoke test.")
    arguments = parser.parse_args()
    build(arguments.skip_build)