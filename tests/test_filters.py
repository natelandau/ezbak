"""Tests for file filtering and ownership helpers."""

from pathlib import Path

from ezbak.filters import chown_files, passes_filters


def test_chown_files_targets_symlink_not_its_target(tmp_path: Path, mocker) -> None:
    """Verify chown is applied to a symlink itself, never to what it points at."""
    # Given: a restored tree containing a symlink pointing outside the tree
    outside_target = tmp_path / "outside.txt"
    outside_target.touch()
    tree = tmp_path / "restored"
    tree.mkdir()
    (tree / "file.txt").touch()
    (tree / "link").symlink_to(outside_target)

    mocker.patch("ezbak.filters.os.getuid", return_value=0, autospec=True)
    lchown = mocker.patch("ezbak.filters.os.lchown", autospec=True)

    # When: changing ownership of the tree
    chown_files(directory=tree, uid=1000, gid=1000)

    # Then: every entry is chowned in place and the symlink's target is untouched
    chowned = {Path(call.kwargs["path"]) for call in lchown.call_args_list}
    assert chowned == {tree / "file.txt", tree / "link"}
    assert outside_target not in chowned


def test_chown_files_continues_after_failure(tmp_path: Path, mocker) -> None:
    """Verify one unchownable file does not abandon the rest of the tree."""
    # Given: a tree of three files where chowning the first fails
    tree = tmp_path / "restored"
    tree.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (tree / name).touch()

    mocker.patch("ezbak.filters.os.getuid", return_value=0, autospec=True)
    lchown = mocker.patch(
        "ezbak.filters.os.lchown",
        autospec=True,
        side_effect=[OSError("nope"), None, None],
    )

    # When: changing ownership of the tree
    chown_files(directory=tree, uid=1000, gid=1000)

    # Then: all three files were attempted despite the first failure
    assert lchown.call_count == 3


def test_passes_filters_excludes_always_excluded_windows_paths() -> None:
    """Verify always-excluded filenames match on Windows-style paths."""
    # Given: an always-excluded filename joined with backslash separators
    # When: evaluating whether the path is included in a backup
    # Then: it is excluded just like its forward-slash spelling
    assert (
        passes_filters(
            path="C:\\backups\\src\\Thumbs.db",
            include_pattern=None,
            exclude_pattern=None,
        )
        is False
    )
    assert (
        passes_filters(
            path="/backups/src/Thumbs.db",
            include_pattern=None,
            exclude_pattern=None,
        )
        is False
    )
    assert (
        passes_filters(
            path="C:\\backups\\src\\doc.txt",
            include_pattern=None,
            exclude_pattern=None,
        )
        is True
    )
