import shutil
import subprocess

import pytest

from dj_music_manager.library import Library
from dj_music_manager.manual import add_file, collect

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg absent")


def make(path, isrc="", artist="Artiste", title="Titre", secs=12):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = ["-metadata", f"artist={artist}", "-metadata", f"title={title}"] + (["-metadata", f"ISRC={isrc}"] if isrc else [])
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"anoisesrc=duration={secs}", *meta, str(path)],
                   check=True)
    return path


@pytest.fixture
def env(tmp_path):
    raw = tmp_path / "RAW"
    raw.mkdir()
    return Library(tmp_path / "db"), raw, tmp_path / "inbox"


def test_copies_into_raw_and_dedups_by_isrc_never_by_name(env):
    lib, raw, inbox = env
    src = make(inbox / "a.flac", isrc="FRXXX0000001")
    created, track_id = add_file(lib, src, raw, FFMPEG, FFPROBE)
    assert created and (raw / "a.flac").exists() and src.exists()  # l'original n'est jamais touché
    t = lib.track(track_id)
    assert (t["source"], t["quality_origin"], t["badge"], t["path"]) == ("manual", "local", "Lossless", "a.flac")

    # même ISRC sous un autre nom : doublon, rien n'est copié
    dup = make(inbox / "autre nom.flac", isrc="FRXXX0000001", title="Autre titre")
    assert add_file(lib, dup, raw, FFMPEG, FFPROBE) == (False, track_id)
    # même nom de fichier, autre son : ajouté à côté, sans écraser
    other = make(inbox / "sub" / "a.flac", isrc="FRXXX0000002", title="Autre son", secs=20)
    created, _ = add_file(lib, other, raw, FFMPEG, FFPROBE)
    assert created and sorted(p.name for p in raw.iterdir()) == ["a (2).flac", "a.flac"]


def test_files_already_in_raw_are_referenced_in_place(env):
    lib, raw, _ = env
    f = make(raw / "dossier" / "x.mp3", artist="", title="")  # sans tags : titre = nom du fichier
    created, track_id = add_file(lib, f, raw, FFMPEG, FFPROBE)
    assert created and lib.track(track_id)["path"] == "dossier/x.mp3" and lib.track(track_id)["title"] == "x"
    assert add_file(lib, f, raw, FFMPEG, FFPROBE) == (False, track_id)  # nouveau scan de RAW
    assert [p.name for p in raw.rglob("*") if p.is_file()] == ["x.mp3"]


def test_collect(tmp_path):
    for name in ("a.mp3", "b.FLAC", "c.txt", "d/e.aiff"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in collect([tmp_path])] == ["a.mp3", "b.FLAC", "e.aiff"]
