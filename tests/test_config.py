import sys

import pytest

from dj_music_manager import config


def make_tools(folder):
    folder.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (folder / name).write_bytes(b"")


def test_ffmpeg_from_settings_then_bundle(tmp_path, monkeypatch):
    make_tools(tmp_path / "ffmpeg")  # disposition du paquet PyInstaller : _internal/ffmpeg
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert config.ffmpeg_tools(None) == (str(tmp_path / "ffmpeg" / "ffmpeg.exe"), str(tmp_path / "ffmpeg" / "ffprobe.exe"))

    make_tools(tmp_path / "perso")
    assert config.ffmpeg_tools(str(tmp_path / "perso"))[0] == str(tmp_path / "perso" / "ffmpeg.exe")  # Réglages d'abord


def test_missing_ffmpeg_explains_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(FileNotFoundError, match="Réglages"):
        config.ffmpeg_tools(None)
