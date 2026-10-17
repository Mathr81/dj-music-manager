"""Export rekordbox.xml, Serato (crates), M3U8 et conversion FLAC -> AIFF."""

import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dj_music_manager import export
from dj_music_manager.library import Library
from dj_music_manager.tags import read_tags

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg absent")


def audio(path: Path, *args, secs=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=duration={secs}", *args, str(path)],
                   check=True)
    return path


@pytest.fixture
def env(tmp_path):
    raw = tmp_path / "RAW"
    lib = Library(tmp_path / "db")
    mp3 = audio(raw / "Été à Paris & co.mp3")
    flac = audio(raw / "sub" / "Hi-Res.flac", "-ar", "96000", "-sample_fmt", "s32", "-c:a", "flac",
                 "-metadata", "artist=Artiste", "-metadata", "title=Hi-Res")
    a, _ = lib.add_track({"path": "Été à Paris & co.mp3", "artist": "Daft Punk", "title": "Été <live>", "album": "A",
                          "duration": 320.4, "bpm": 123, "format": "mp3", "source": "manual"})
    b, _ = lib.add_track({"path": "sub/Hi-Res.flac", "artist": "Artiste", "title": "Hi-Res", "duration": 2.0,
                          "format": "flac", "source": "manual"})
    gone, _ = lib.add_track({"path": "supprimé.mp3", "artist": "X", "title": "Disparu", "source": "manual"})
    lib.add_tags([a], ["house", "peak"])
    p1 = lib.create_playlist("Set samedi")
    lib.add_to_playlist(p1, [b, a, gone])
    p2 = lib.create_playlist("set samedi")  # même nom à la casse près
    lib.add_to_playlist(p2, [a])
    return lib, raw, [p1, p2], (a, b), (mp3, flac)


def test_gather_skips_missing_files(env):
    lib, raw, pids, (a, b), _ = env
    playlists, missing = export.gather(lib, pids, raw)
    assert [it.id for it in playlists[0][1]] == [b, a]  # ordre de la playlist
    assert missing == ["Set samedi : X – Disparu"]


def test_rekordbox_xml_follows_pioneer_format(env):
    lib, raw, pids, (a, b), (mp3, flac) = env
    playlists, _ = export.gather(lib, pids, raw)
    root = ET.fromstring(export.rekordbox_xml(playlists, "0.1.0"))

    assert root.tag == "DJ_PLAYLISTS" and root.find("PRODUCT").get("Name") == "dj-music-manager"
    collection = root.find("COLLECTION")
    assert collection.get("Entries") == "2"
    t = {e.get("TrackID"): e for e in collection}[str(a)]
    assert (t.get("Name"), t.get("Kind"), t.get("TotalTime"), t.get("AverageBpm"), t.get("Comments")) == (
        "Été <live>", "MP3 File", "320", "123.00", "house, peak")
    assert t.get("Location") == "file://localhost/" + mp3.resolve().as_posix().replace(
        " ", "%20").replace("É", "%C3%89").replace("é", "%C3%A9").replace("à", "%C3%A0").replace("&", "%26")
    assert t.get("Size") == str(mp3.stat().st_size) and len(t.get("DateAdded")) == 10

    top = root.find("PLAYLISTS/NODE")
    assert (top.get("Type"), top.get("Name"), top.get("Count")) == ("0", "ROOT", "2")
    first, second = top.findall("NODE")
    assert (first.get("Name"), first.get("Type"), first.get("KeyType"), first.get("Entries")) == (
        "Set samedi", "1", "0", "2")
    assert [k.get("Key") for k in first] == [str(b), str(a)]
    assert second.get("Name") == "set samedi (2)"  # rekordbox refuse deux noms identiques au même niveau


def test_m3u8(env, tmp_path):
    lib, raw, pids, _, (mp3, flac) = env
    playlists, _ = export.gather(lib, pids, raw)
    first, second = export.write_m3u8(tmp_path / "out", playlists)
    assert (first.name, second.name) == ("Set samedi.m3u8", "set samedi (2).m3u8")
    assert first.read_text(encoding="utf-8").splitlines() == [
        "#EXTM3U", "#EXTINF:2,Artiste - Hi-Res", str(flac.resolve()),
        "#EXTINF:320,Daft Punk - Été <live>", str(mp3.resolve())]


def read_crate(path: Path) -> dict:
    """Lecture façon Mixxx : champs tag (4 o) + longueur big-endian (4 o) + valeur."""
    def fields(data):
        i = 0
        while i < len(data):
            tag, size = data[i:i + 4].decode(), struct.unpack(">I", data[i + 4:i + 8])[0]
            yield tag, data[i + 8:i + 8 + size]
            i += 8 + size

    out = {"tracks": []}
    for tag, value in fields(path.read_bytes()):
        if tag == "vrsn":
            out["vrsn"] = value.decode("utf-16-be")
        elif tag == "otrk":
            out["tracks"] += [v.decode("utf-16-be") for t, v in fields(value) if t == "ptrk"]
    return out


def test_serato_crates_go_to_the_files_drive_and_touch_nothing_else(env, tmp_path, monkeypatch):
    lib, raw, pids, _, (mp3, flac) = env
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SystemDrive", tmp_path.drive)  # les fichiers de test sont sur le disque système
    subcrates = home / "Music" / "_Serato_" / "Subcrates"
    other = subcrates / "Mes crates.crate"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"ne pas toucher")

    playlists, _ = export.gather(lib, pids, raw)
    written = export.write_serato(playlists)

    assert [p.name for p in written] == ["dj-music-manager%%Set samedi.crate", "dj-music-manager%%set samedi (2).crate"]
    crate = read_crate(written[0])
    assert crate["vrsn"] == "1.0/Serato ScratchLive Crate"
    drive_root = Path(tmp_path.drive + "\\")
    assert crate["tracks"] == [flac.resolve().relative_to(drive_root).as_posix(),
                               mp3.resolve().relative_to(drive_root).as_posix()]
    assert not crate["tracks"][0].startswith("/") and "\\" not in crate["tracks"][0]
    assert (subcrates / "dj-music-manager.crate").exists()
    assert other.read_bytes() == b"ne pas toucher"


def test_serato_location_other_drive(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    assert export.serato_location(Path("D:/DJing/RAW/a b.mp3")) == (Path("D:/_Serato_/Subcrates"), "DJing/RAW/a b.mp3")


def test_flac_converted_to_aiff_for_old_cdjs(env, tmp_path):
    lib, raw, pids, (a, b), (mp3, flac) = env
    playlists, _ = export.gather(lib, pids, raw)
    aiff_dir = tmp_path / "RAW_AIFF"
    converted = export.convert_flacs(playlists, raw, aiff_dir, FFMPEG, FFPROBE)

    item = converted[0][1][0]
    assert item.path == (aiff_dir / "sub" / "Hi-Res.aiff").resolve() and item.format == "aiff"
    assert converted[0][1][1].path == mp3.resolve()  # le MP3 ne bouge pas
    probe = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_name,sample_rate", "-of", "csv=p=0",
                            str(item.path)], capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "pcm_s24be,48000"  # 24 bits conservés, 96 kHz ramenés à 48 kHz
    assert read_tags(item.path)["title"] == "Hi-Res"
    assert flac.exists() and sorted(p.name for p in raw.rglob("*") if p.is_file()) == ["Hi-Res.flac", "Été à Paris & co.mp3"]

    mtime = item.path.stat().st_mtime_ns
    export.convert_flacs(playlists, raw, aiff_dir, FFMPEG, FFPROBE)
    assert item.path.stat().st_mtime_ns == mtime  # conversion réutilisée


def test_run_all_formats_with_conversion(env, tmp_path, monkeypatch):
    lib, raw, pids, _, _ = env
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("SystemDrive", tmp_path.drive)
    result = export.run(lib, pids[:1], raw, ["rekordbox", "serato", "m3u8"], tmp_path / "Export DJ",
                        tmp_path / "RAW_AIFF", FFMPEG, FFPROBE, version="0.1.0")
    assert result["rekordbox"] == [tmp_path / "Export DJ" / "rekordbox.xml"]
    assert [p.name for p in result["m3u8"]] == ["Set samedi.m3u8"]
    assert len(result["serato"]) == 1 and result["missing"] == ["Set samedi : X – Disparu"]
    xml = result["rekordbox"][0].read_text(encoding="utf-8")
    assert "Hi-Res.aiff" in xml and "AIFF File" in xml and "Hi-Res.flac" not in xml  # le XML pointe vers l'AIFF
