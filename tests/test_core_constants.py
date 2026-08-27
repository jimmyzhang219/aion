from unittest.mock import patch
from aion.core.constants import get_trash_path


def test_trash_path_darwin():
    with patch("sys.platform", "darwin"):
        p = get_trash_path()
        assert p.name == ".Trash"


def test_trash_path_linux():
    with patch("sys.platform", "linux"):
        p = get_trash_path()
        assert p.parts[-4:] == (".local", "share", "Trash", "files")


def test_trash_path_win32_fallback():
    with patch("sys.platform", "win32"):
        p = get_trash_path()
        assert p.parts[-1] == ".aion_trash"
        assert "AppData" in p.parts
