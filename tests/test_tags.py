import shutil
import subprocess

import pytest
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TBPM, TIT2, TKEY, TPE1, TSRC
from mutagen.mp4 import MP4, MP4FreeForm

from dj_music_manager.tags import read_tags

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")


def sine(path, *codec):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", *codec, str(path)], check=True)
    return path


def test_aiff_id3_tags_are_read(tmp_path):
    """Format d'achat courant : mutagen n'a pas de variante « easy » pour l'ID3 des AIFF/WAV."""
    a = AIFF(sine(tmp_path / "x.aiff"))
    a.add_tags()
    for frame in (TPE1(encoding=3, text=["Daft Punk"]), TIT2(encoding=3, text=["One More Time"]),
                  TSRC(encoding=3, text=["GBDUW0000053"]), TBPM(encoding=3, text=["123"])):
        a.tags.add(frame)
    a.save()
    t = read_tags(tmp_path / "x.aiff")
    assert (t["artist"], t["title"], t["isrc"], t["bpm"], t["format"]) == (
        "Daft Punk", "One More Time", "GBDUW0000053", 123.0, "aiff")


def test_m4a_isrc_is_read(tmp_path):
    m = MP4(sine(tmp_path / "x.m4a", "-c:a", "aac"))
    m.tags["----:com.apple.iTunes:ISRC"] = [MP4FreeForm(b"GBDUW0000053")]
    m.save()
    assert read_tags(tmp_path / "x.m4a")["isrc"] == "GBDUW0000053"


def test_key_tag_is_read_in_every_notation(tmp_path):
    """TKEY (MP3, AIFF) et INITIALKEY (FLAC), écrits par rekordbox, Mixed In Key ou Traktor."""
    mp3 = sine(tmp_path / "x.mp3")
    tags = ID3()
    tags.add(TKEY(encoding=3, text=["8A"]))
    tags.save(mp3)
    a = AIFF(sine(tmp_path / "x.aiff"))
    a.add_tags()
    a.tags.add(TKEY(encoding=3, text=["F#m"]))
    a.save()
    f = FLAC(sine(tmp_path / "x.flac"))
    f["INITIALKEY"] = "Db"
    f.save()
    assert [read_tags(p)["key"] for p in (mp3, tmp_path / "x.aiff", tmp_path / "x.flac")] == ["Am", "F#m", "Db"]
    assert read_tags(sine(tmp_path / "y.mp3"))["key"] is None
