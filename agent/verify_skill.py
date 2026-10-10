"""Image build step: verify the pinned powerpoint skill files byte-for-byte
against their git blob SHAs at Hermes 818c13be (images/skill-blobs.txt).
Fails the build (non-zero) on any mismatch or missing file."""
import hashlib
import sys
from pathlib import Path


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main(listing: str, root: str) -> int:
    bad = 0
    for line in Path(listing).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        sha, rel = line.split(None, 1)
        p = Path(root) / rel.removeprefix("skills/productivity/powerpoint/")
        try:
            got = blob_sha(p.read_bytes())
        except OSError as e:
            print(f"MISSING {rel}: {e}")
            bad += 1
            continue
        ok = got == sha
        bad += not ok
        print(f"{'OK  ' if ok else 'BAD '} {sha} {rel}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
