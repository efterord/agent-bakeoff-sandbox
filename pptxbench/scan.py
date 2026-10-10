"""Host-side handling of untrusted sandbox output (stdlib only).

* safe_extract: copy only the allowed names out of a sandbox's out/ dir, using
  lstat + O_NOFOLLOW; symlinks, hard links, FIFOs, devices, sockets and
  oversize files are refused and reported.
* zip_guard: package-level checks before anything parses the deck (magic,
  entry count, uncompressed size, compression ratio, path traversal).
* scrub_props / identifying_strings: blinding helpers (normalize docProps and
  flag runtime-identifying strings) used before judge/human packaging.
"""
from __future__ import annotations

import io
import os
import re
import stat
import zipfile
from pathlib import Path

MAX_DELIVERABLE = 25 * 1024 * 1024
MAX_TRACE_TOTAL = 20 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_RATIO = 100.0

ALLOWED_TOP = {"deliverable.pptx", "canary.txt", "trace", "driver.json"}


def _read_nofollow(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_size > limit:
            raise ValueError(f"too large ({st.st_size} > {limit})")
        chunks, n = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            n += len(b)
            if n > limit:
                raise ValueError("grew past limit while reading")
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _check_entry(p: Path) -> str | None:
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISFIFO(st.st_mode):
        return "fifo"
    if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        return "device"
    if stat.S_ISSOCK(st.st_mode):
        return "socket"
    if stat.S_ISREG(st.st_mode) and st.st_nlink != 1:
        return "hard link"
    return None


def safe_extract(out_dir: Path, dest: Path) -> dict:
    """Returns {"copied": [...], "refused": [{name, reason}], "canary": bool}."""
    dest.mkdir(parents=True, exist_ok=True)
    res = {"copied": [], "refused": [], "canary": False}
    if not out_dir.is_dir():
        res["refused"].append({"name": str(out_dir), "reason": "out dir missing"})
        return res
    for name in sorted(os.listdir(out_dir)):
        p = out_dir / name
        bad = _check_entry(p)
        if name == "w":  # agent scratch: never copied, but planted specials there are still reported
            for root, dirs, files in os.walk(p, followlinks=False):
                for n in dirs + files:
                    why = _check_entry(Path(root) / n)
                    if why:
                        res["refused"].append({"name": f"w/{os.path.relpath(Path(root) / n, p)}", "reason": why})
            continue
        if name not in ALLOWED_TOP:
            res["refused"].append({"name": name, "reason": bad or "name not allowed"})
            continue
        if bad:
            res["refused"].append({"name": name, "reason": bad})
            continue
        if name == "trace":
            if not stat.S_ISDIR(os.lstat(p).st_mode):
                res["refused"].append({"name": name, "reason": "trace is not a directory"})
                continue
            total = 0
            (dest / "trace").mkdir(exist_ok=True)
            for tn in sorted(os.listdir(p)):
                tp = p / tn
                why = _check_entry(tp)
                if why or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}\.jsonl?", tn):
                    res["refused"].append({"name": f"trace/{tn}", "reason": why or "name not allowed"})
                    continue
                try:
                    data = _read_nofollow(tp, MAX_TRACE_TOTAL - total)
                except (OSError, ValueError) as e:
                    res["refused"].append({"name": f"trace/{tn}", "reason": str(e)})
                    continue
                total += len(data)
                (dest / "trace" / tn).write_bytes(data)
                res["copied"].append(f"trace/{tn}")
            continue
        limit = MAX_DELIVERABLE if name == "deliverable.pptx" else 64 * 1024
        try:
            data = _read_nofollow(p, limit)
        except (OSError, ValueError) as e:
            res["refused"].append({"name": name, "reason": str(e)})
            continue
        (dest / name).write_bytes(data)
        res["copied"].append(name)
        if name == "canary.txt":
            res["canary"] = True
    return res


def zip_guard(path: Path) -> list[str]:
    """Findings that make a package unsafe to parse. Empty list = ok."""
    f = []
    try:
        head = path.read_bytes()[:4]
    except OSError as e:
        return [f"unreadable: {e}"]
    if head != b"PK\x03\x04":
        return ["not a zip (bad magic)"]
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                f.append(f"too many entries ({len(infos)})")
            total = sum(i.file_size for i in infos)
            comp = sum(max(1, i.compress_size) for i in infos)
            if total > MAX_UNCOMPRESSED:
                f.append(f"uncompressed size {total} > {MAX_UNCOMPRESSED}")
            if total / comp > MAX_RATIO:
                f.append(f"compression ratio {total / comp:.0f} > {MAX_RATIO:.0f}")
            for i in infos:
                n = i.filename
                if n.startswith(("/", "\\")) or ".." in n.split("/") or "\\" in n or ":" in n:
                    f.append(f"path traversal name {n!r}")
                if i.file_size > 1024 * max(1, i.compress_size) and i.file_size > 1 << 20:
                    f.append(f"entry ratio too high: {n}")
    except zipfile.BadZipFile as e:
        f.append(f"bad zip: {e}")
    return f


IDENTIFYING = re.compile(r"hermes|nous ?research|python-pptx|pptx_(create|edit|read|render|from_template|inspect|shape_ops|check)|typed[-_ ]tools?|skill_view", re.I)

CORE_XML = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            b'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>deck</dc:title>'
            b'<dc:creator>anonymous</dc:creator><cp:lastModifiedBy>anonymous</cp:lastModifiedBy>'
            b'<cp:revision>1</cp:revision></cp:coreProperties>')
APP_XML = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           b'<Application>anonymous</Application></Properties>')
CUSTOM_XML = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
              b'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>')


def scrub_props(src: Path, dst: Path) -> list[str]:
    """Copy the package with docProps normalized; return identifying strings left in slide XML."""
    flags = []
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = CORE_XML
            elif item.filename == "docProps/app.xml":
                data = APP_XML
            elif item.filename == "docProps/custom.xml":
                data = CUSTOM_XML
            elif item.filename.endswith((".xml", ".rels")):
                for m in IDENTIFYING.finditer(data.decode("utf-8", "replace")):
                    flags.append(f"{item.filename}: {m.group(0)}")
            zo.writestr(item, data)
    return flags[:20]


def identifying_strings(data: bytes | str) -> list[str]:
    s = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return sorted({m.group(0) for m in IDENTIFYING.finditer(s)})


def make_zip_bomb(path: Path, mb: int = 300) -> Path:
    """Negative-control fixture: a small zip that expands past the limits."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        with z.open("ppt/bomb.bin", "w", force_zip64=True) as w:
            chunk = b"\0" * (1 << 20)
            for _ in range(mb):
                w.write(chunk)
    return path


def make_traversal_zip(path: Path) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("../../escape.txt", "x")
    path.write_bytes(buf.getvalue())
    return path
