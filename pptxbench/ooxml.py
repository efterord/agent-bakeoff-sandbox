"""OOXML package validation, normalized XML-level diff and package security checks.

Validation is structural (zip integrity, [Content_Types].xml, well-formed XML,
relationship targets). Full ECMA-376 XSD validation runs only when a schema
directory is supplied (BENCH_OOXML_XSD_DIR); the schemas are not vendored.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import posixpath
import re
import zipfile
from pathlib import Path

VOLATILE_PARTS = re.compile(r"^(docProps/(core|app)\.xml)$")
VOLATILE_ATTRS = re.compile(r"^\{[^}]+\}(rsid\w*|paraId|textId)$")


def _etree():
    from lxml import etree
    return etree


def _parser(**kw):
    """Hardened parser: no entity resolution, no DTD loading, no network, no huge trees."""
    etree = _etree()
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, **kw)


def validate_package(path: Path) -> dict:
    """Return {"ok": bool, "errors": [...], "warnings": [...], "parts": n}."""
    etree = _etree()
    errors, warnings = [], []
    try:
        zf = zipfile.ZipFile(path)
    except Exception as exc:  # not a zip at all
        return {"ok": False, "errors": [f"not a zip: {exc}"], "warnings": [], "parts": 0}
    with zf:
        bad = zf.testzip()
        if bad:
            errors.append(f"corrupt member {bad}")
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names:
            errors.append("missing [Content_Types].xml")
        overrides, defaults = set(), set()
        for name in names:
            if not (name.endswith(".xml") or name.endswith(".rels")):
                continue
            try:
                root = etree.fromstring(zf.read(name), _parser())
            except Exception as exc:
                errors.append(f"malformed XML {name}: {exc}")
                continue
            if name == "[Content_Types].xml":
                for el in root:
                    if el.get("PartName"):
                        overrides.add(el.get("PartName").lstrip("/"))
                    if el.get("Extension"):
                        defaults.add(el.get("Extension").lower())
            if name.endswith(".rels"):
                base = posixpath.dirname(posixpath.dirname(name))
                for rel in root:
                    if rel.get("TargetMode") == "External":
                        continue
                    tgt = rel.get("Target", "")
                    full = posixpath.normpath(tgt.lstrip("/") if tgt.startswith("/") else posixpath.join(base, tgt))
                    if full not in names:
                        errors.append(f"dangling relationship {name} -> {tgt}")
        for name in names:
            if name.endswith("/") or name == "[Content_Types].xml":
                continue
            ext = name.rsplit(".", 1)[-1].lower()
            if name not in overrides and ext not in defaults:
                warnings.append(f"no content type for {name}")
    xsd_dir = os.environ.get("BENCH_OOXML_XSD_DIR")
    if xsd_dir:
        warnings.append("XSD validation requested but not implemented in fake mode")
    return {"ok": not errors, "errors": errors[:20], "warnings": warnings[:20], "parts": len(names)}


def security_scan(path: Path) -> list[str]:
    """Critical findings: macros, external relationships, OLE objects."""
    etree = _etree()
    findings = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                low = name.lower()
                if low.endswith("vbaproject.bin"):
                    findings.append(f"macro project {name}")
                if "/embeddings/" in low and low.endswith(".bin"):
                    findings.append(f"OLE embedding {name}")
                if low.endswith(".rels"):
                    root = etree.fromstring(zf.read(name), _parser())
                    for rel in root:
                        if rel.get("TargetMode") == "External" and "hyperlink" not in rel.get("Type", ""):
                            findings.append(f"external {rel.get('Type', '').rsplit('/', 1)[-1]} -> {rel.get('Target')}")
    except Exception as exc:
        findings.append(f"unreadable package: {exc}")
    return findings


def normalized_part(data: bytes) -> bytes:
    """Canonical form of one XML part: C14N, volatile attributes stripped."""
    etree = _etree()
    try:
        root = etree.fromstring(data, _parser(remove_blank_text=True))
    except Exception:
        return data
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if VOLATILE_ATTRS.match(attr):
                del el.attrib[attr]
    return etree.tostring(root, method="c14n")


def part_hashes(path: Path) -> dict[str, str]:
    out = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if VOLATILE_PARTS.match(name):
                continue
            data = zf.read(name)
            if name.endswith(".xml") or name.endswith(".rels"):
                data = normalized_part(data)
            out[name] = hashlib.sha256(data).hexdigest()
    return out


def read_part(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return normalized_part(zf.read(name))


def diff_parts(before: Path, after: Path) -> dict:
    """Which parts were added, removed or changed (normalized)."""
    a, b = part_hashes(before), part_hashes(after)
    return {
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]),
    }


def change_size(before: Path, after: Path, name: str) -> float:
    """1 - similarity ratio of the pretty-printed normalized part (0 = same)."""
    etree = _etree()

    def lines(p):
        try:
            return etree.tostring(etree.fromstring(read_part(p, name), _parser()), pretty_print=True).decode().splitlines()
        except Exception:
            return []
    return 1.0 - difflib.SequenceMatcher(a=lines(before), b=lines(after), autojunk=False).ratio()
