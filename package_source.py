"""Build a source + demo archive from an explicit allowlist (never runtime data)."""
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = ["app.py", "README.md", ".gitignore", ".env.example", "requirements-dev.txt", "package_source.py"]
DIRECTORIES = ["worker", "static", "tests", "docs"]
ARTIFACTS = ["home.png", "verified-run.png", "approval.png", "ledger.png",
             "responsive-390.png", "responsive-768.png", "demo-traces.json", "relay-demo.webm"]


def main():
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    paths.extend(ROOT / "artifacts" / name for name in ARTIFACTS)
    destination = ROOT / "artifacts" / "relay-source.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "relay/" + path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (destination.parent / "relay-source.sha256").write_text(f"{digest}  relay-source.zip\n", encoding="utf-8")
    print(f"Packaged {len(paths)} files: {destination} ({destination.stat().st_size:,} bytes)")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
