"""Export des playlists pour les logiciels DJ, sans jamais écrire dans leurs bases de données.

- rekordbox : fichier rekordbox.xml (format « playlists sharing » de Pioneer), à importer dans rekordbox.
- Serato : nos propres crates « dj-music-manager%%<playlist>.crate » dans le dossier _Serato_ du disque des fichiers
  (format binaire lu aussi par Mixxx ; référence : cratedigger et la lecture des crates dans Mixxx).
- VirtualDJ, Mixxx et les autres : playlists .m3u8 (UTF-8).
Option : les FLAC sont convertis en AIFF (anciens CDJ) dans un dossier à part, jamais dans RAW.
"""

import json
import os
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from .library import Library

APP = "dj-music-manager"
KINDS = {"mp3": "MP3 File", "flac": "FLAC File", "aiff": "AIFF File", "aif": "AIFF File", "wav": "WAV File",
         "m4a": "M4A File", "aac": "AAC File"}
SERATO_COLUMNS = ("song", "artist", "album", "length", "bpm", "comment")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Item:
    """Un son prêt à exporter (chemin absolu du fichier réellement exporté)."""
    id: int
    path: Path
    artist: str
    title: str
    album: str
    duration: float
    bpm: float | None
    format: str
    tags: tuple[str, ...]
    added_at: str


Playlists = list[tuple[str, list[Item]]]


def gather(lib: Library, playlist_ids: list[int], raw: Path) -> tuple[Playlists, list[str]]:
    """(playlists dans l'ordre, sons dont le fichier est introuvable)."""
    tags = lib.tags_by_track()
    playlists, missing = [], []
    for pid in playlist_ids:
        p = lib.playlist(pid)
        items = []
        for track_id in lib.playlist_track_ids(pid):
            t = lib.track(track_id)
            path = (Path(raw) / t["path"]).resolve()
            if not path.is_file():
                missing.append(f"{p['name']} : {t['artist']} – {t['title']}")
                continue
            items.append(Item(t["id"], path, t["artist"] or "", t["title"] or path.stem, t["album"] or "",
                              t["duration"] or 0.0, t["bpm"], (t["format"] or path.suffix[1:]).lower(),
                              tuple(tags.get(track_id, [])), t["added_at"]))
        playlists.append((p["name"], items))
    return playlists, missing


# --- FLAC -> AIFF pour les anciens CDJ
def to_aiff(src: Path, dst: Path, ffmpeg: str, ffprobe: str) -> Path:
    """Convertit en AIFF (16 ou 24 bits selon la source, 48 kHz max), tags copiés. Réutilise une conversion à jour."""
    if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=bits_per_raw_sample,sample_rate", "-of", "json", str(src)],
                         capture_output=True, text=True, check=True, creationflags=_NO_WINDOW).stdout
    stream = (json.loads(out).get("streams") or [{}])[0]
    bits = int(stream.get("bits_per_raw_sample") or 16)
    rate = int(stream.get("sample_rate") or 44100)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    # shortcut: la pochette n'est pas recopiée dans l'AIFF ; rekordbox la relit depuis le fichier d'origine si besoin
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-map_metadata", "0",
                    "-c:a", "pcm_s24be" if bits > 16 else "pcm_s16be", *(["-ar", "48000"] if rate > 48000 else []),
                    "-write_id3v2", "1", "-f", "aiff", str(part)],
                   capture_output=True, check=True, creationflags=_NO_WINDOW)
    os.replace(part, dst)
    return dst


def convert_flacs(playlists: Playlists, raw: Path, aiff_dir: Path, ffmpeg: str, ffprobe: str,
                  on_progress=None) -> Playlists:
    """Remplace les FLAC par leur version AIFF (même chemin relatif que dans RAW, sous aiff_dir)."""
    flacs = {it.id: it for _, items in playlists for it in items if it.format == "flac"}
    converted = {}
    for n, it in enumerate(flacs.values(), 1):
        rel = it.path.relative_to(Path(raw).resolve()) if it.path.is_relative_to(Path(raw).resolve()) \
            else Path(it.path.name)
        converted[it.id] = replace(it, path=to_aiff(it.path, Path(aiff_dir) / rel.with_suffix(".aiff"), ffmpeg,
                                                    ffprobe).resolve(), format="aiff")
        if on_progress:
            on_progress(n, len(flacs))
    return [(name, [converted.get(it.id, it) for it in items]) for name, items in playlists]


# --- utilitaires
def _unique_names(playlists: Playlists) -> Playlists:
    """rekordbox refuse deux playlists du même nom au même niveau ; on numérote les doublons."""
    seen: dict[str, int] = {}
    out = []
    for name, items in playlists:
        seen[name.casefold()] = seen.get(name.casefold(), 0) + 1
        out.append((name if seen[name.casefold()] == 1 else f"{name} ({seen[name.casefold()]})", items))
    return out


def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*%\x00-\x1f]', "_", name).strip(" .") or "playlist"


def _write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


# --- rekordbox
def file_uri(path: Path) -> str:
    """'D:\\DJing\\RAW\\Été.mp3' -> 'file://localhost/D:/DJing/RAW/%C3%89t%C3%A9.mp3'."""
    return "file://localhost/" + quote(Path(path).resolve().as_posix().lstrip("/"), safe="/:")


def rekordbox_xml(playlists: Playlists, version: str = "") -> bytes:
    playlists = _unique_names(playlists)
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name=APP, Version=version, Company="")
    tracks = {it.id: it for _, items in playlists for it in items}
    collection = ET.SubElement(root, "COLLECTION", Entries=str(len(tracks)))
    for it in tracks.values():
        ET.SubElement(collection, "TRACK", {
            "TrackID": str(it.id), "Name": it.title, "Artist": it.artist, "Album": it.album,
            "Kind": KINDS.get(it.format, f"{it.format.upper()} File"), "Size": str(it.path.stat().st_size),
            "TotalTime": str(round(it.duration)), "AverageBpm": f"{it.bpm or 0:.2f}",
            "DateAdded": it.added_at[:10], "Comments": ", ".join(it.tags), "Location": file_uri(it.path),
        })
    nodes = ET.SubElement(root, "PLAYLISTS")
    top = ET.SubElement(nodes, "NODE", Type="0", Name="ROOT", Count=str(len(playlists)))
    for name, items in playlists:
        node = ET.SubElement(top, "NODE", Name=name, Type="1", KeyType="0", Entries=str(len(items)))
        for it in items:
            ET.SubElement(node, "TRACK", Key=str(it.id))
    ET.indent(root)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def write_rekordbox(path: Path, playlists: Playlists, version: str = "") -> Path:
    _write_atomic(Path(path), rekordbox_xml(playlists, version))
    return Path(path)


# --- M3U8 (VirtualDJ, Mixxx, Traktor, Engine DJ…)
def write_m3u8(dest_dir: Path, playlists: Playlists) -> list[Path]:
    written = []
    for name, items in _unique_names(playlists):
        lines = ["#EXTM3U"]
        for it in items:
            lines += [f"#EXTINF:{round(it.duration)},{it.artist} - {it.title}", str(it.path)]
        path = Path(dest_dir) / f"{_safe_filename(name)}.m3u8"
        _write_atomic(path, ("\n".join(lines) + "\n").encode("utf-8"))
        written.append(path)
    return written


# --- Serato
def _tlv(tag: bytes, data: bytes) -> bytes:
    return tag + struct.pack(">I", len(data)) + data


def _utf16(s: str) -> bytes:
    return s.encode("utf-16-be")


def crate_bytes(track_paths: list[str]) -> bytes:
    """Crate Serato : champs tag/longueur (big-endian)/valeur, textes en UTF-16BE."""
    data = _tlv(b"vrsn", _utf16("1.0/Serato ScratchLive Crate"))
    data += _tlv(b"osrt", _tlv(b"tvcn", _utf16("song")) + _tlv(b"brev", b"\x00"))
    for column in SERATO_COLUMNS:
        data += _tlv(b"ovct", _tlv(b"tvcn", _utf16(column)) + _tlv(b"tvcw", _utf16("0")))
    for p in track_paths:
        data += _tlv(b"otrk", _tlv(b"ptrk", _utf16(p)))
    return data


def serato_location(path: Path) -> tuple[Path, str]:
    """(dossier Subcrates du disque du fichier, chemin du fichier relatif à la racine du disque, en '/')."""
    path = Path(path).resolve()
    drive = path.drive
    system_drive = os.environ.get("SystemDrive", "C:")
    subcrates = (Path.home() / "Music" / "_Serato_" / "Subcrates" if drive.upper() == system_drive.upper()
                 else Path(drive + "\\") / "_Serato_" / "Subcrates")
    return subcrates, path.relative_to(Path(drive + "\\")).as_posix()


def write_serato(playlists: Playlists) -> list[Path]:
    """Une crate par playlist et par disque, rangée sous la crate « dj-music-manager » ; rien d'autre n'est touché."""
    written = []
    for name, items in _unique_names(playlists):
        by_dir: dict[Path, list[str]] = {}
        for it in items:
            subcrates, rel = serato_location(it.path)
            by_dir.setdefault(subcrates, []).append(rel)
        for subcrates, paths in by_dir.items():
            parent = subcrates / f"{APP}.crate"
            if not parent.exists():
                _write_atomic(parent, crate_bytes([]))
            crate = subcrates / f"{APP}%%{_safe_filename(name)}.crate"
            _write_atomic(crate, crate_bytes(paths))
            written.append(crate)
    return written


def run(lib: Library, playlist_ids: list[int], raw: Path, formats: list[str], export_dir: Path,
        aiff_dir: Path | None = None, ffmpeg: str | None = None, ffprobe: str | None = None,
        on_progress=None, version: str = "") -> dict:
    """Export complet. Renvoie {"rekordbox": [xml], "serato": [crates], "m3u8": [fichiers], "missing": [...]}."""
    playlists, missing = gather(lib, playlist_ids, raw)
    if aiff_dir is not None:
        playlists = convert_flacs(playlists, raw, aiff_dir, ffmpeg, ffprobe, on_progress)
    result = {"missing": missing}
    if "rekordbox" in formats:
        result["rekordbox"] = [write_rekordbox(Path(export_dir) / "rekordbox.xml", playlists, version)]
    if "serato" in formats:
        result["serato"] = write_serato(playlists)
    if "m3u8" in formats:
        result["m3u8"] = write_m3u8(Path(export_dir) / "M3U8", playlists)
    return result
