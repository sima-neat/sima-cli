"""Local netboot image discovery, cache reuse, cleanup, and CLI validation."""

import gzip
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sima_cli.cli import bootimg_cmd
from sima_cli.update import netboot


def _write_tar(path: Path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def _directory_contents(root: Path):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def test_elxr_images_directory_is_read_only_and_reuses_cache(tmp_path):
    images = tmp_path / "images"
    nested = images / "release"
    nested.mkdir(parents=True)
    archive = nested / "modalix-tftp-boot-minimal.tar.gz"
    emmc = nested / "elxr-palette-modalix-3.0.0-arm64.img.gz"
    _write_tar(archive, {"Image": b"kernel", "boot/troot_blob.be": b"troot"})
    emmc.write_bytes(gzip.compress(b"disk image"))
    original = _directory_contents(images)

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path / "tmp")):
        first = netboot._prepare_local_netboot_assets(
            str(images), "modalix", "elxr", "headless"
        )
        with patch.object(netboot, "_extract_required_files") as extract:
            second = netboot._prepare_local_netboot_assets(
                str(images), "modalix", "elxr", "headless"
            )

    assert first.reused_cache is False
    assert second.reused_cache is True
    assert second.cache_dir == first.cache_dir
    assert second.emmc_image_paths == [str(emmc)]
    assert Path(second.troot_image_path).read_bytes() == b"troot"
    assert Path(second.tftp_root, "Image").read_bytes() == b"kernel"
    assert _directory_contents(images) == original
    extract.assert_not_called()


def test_yocto_images_directory_finds_release_bundle(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    _write_tar(
        images / "release.tar.gz",
        {
            "netboot.scr.uimg": b"script",
            "simaai-image-palette-modalix.wic.gz": gzip.compress(b"wic"),
            "simaai-image-palette-modalix.wic.bmap": b"bmap",
        },
    )

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path / "tmp")):
        assets = netboot._prepare_local_netboot_assets(
            str(images), "modalix", "yocto", "headless"
        )

    assert [Path(path).suffixes for path in assets.emmc_image_paths] == [
        [".wic", ".gz"], [".wic", ".bmap"]
    ]
    assert Path(assets.tftp_root, "netboot.scr.uimg").is_file()


def test_local_archive_preserves_nested_tftp_root(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    _write_tar(
        images / "modalix-tftp-boot-minimal.tar.gz",
        {
            "README.txt": b"metadata before the boot directory",
            "boot/netboot.scr.uimg": b"script",
            "boot/Image": b"kernel",
        },
    )
    (images / "elxr-palette-modalix-3.0.0-arm64.img.gz").write_bytes(b"image")

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path / "tmp")):
        assets = netboot._prepare_local_netboot_assets(
            str(images), "modalix", "elxr", "headless"
        )

    assert Path(assets.tftp_root).name == "boot"
    assert Path(assets.tftp_root, "netboot.scr.uimg").is_file()


def test_explicit_local_archive_is_not_replaced_by_sibling(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    selected = images / "custom-netboot.tar.gz"
    _write_tar(
        selected,
        {
            "netboot.scr.uimg": b"selected",
            "simaai-image-palette-modalix.wic.gz": b"selected-wic",
            "simaai-image-palette-modalix.wic.bmap": b"selected-bmap",
        },
    )
    _write_tar(
        images / "release.tar.gz",
        {
            "netboot.scr.uimg": b"sibling",
            "simaai-image-palette-modalix.wic.gz": b"sibling-wic",
            "simaai-image-palette-modalix.wic.bmap": b"sibling-bmap",
        },
    )

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path / "tmp")):
        assets = netboot._prepare_netboot_assets(
            str(selected), "modalix", "yocto", False, "headless"
        )

    assert Path(assets.tftp_root, "netboot.scr.uimg").read_bytes() == b"selected"


def test_images_directory_rejects_ambiguous_elxr_emmc_images(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    _write_tar(images / "modalix-tftp-boot-minimal.tar.gz", {"Image": b"kernel"})
    (images / "first.img.gz").write_bytes(b"first")
    (images / "second.img.gz").write_bytes(b"second")

    with pytest.raises(RuntimeError, match="Expected exactly one eLxr eMMC"):
        netboot._prepare_local_netboot_assets(
            str(images), "modalix", "elxr", "headless"
        )


def test_downloaded_netboot_assets_are_prepared_once(tmp_path):
    def fake_download(_version, _board, **kwargs):
        destination = Path(kwargs["destination_dir"])
        (destination / "Image").write_bytes(b"kernel")
        (destination / "root.wic.gz").write_bytes(b"wic")
        (destination / "root.wic.bmap").write_bytes(b"bmap")
        return [str(path) for path in destination.iterdir()]

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path)), \
            patch.object(netboot, "download_image", side_effect=fake_download) as download:
        first = netboot._prepare_downloaded_netboot_assets(
            "https://example.com/release.tar.gz", "modalix", "yocto", False, "headless"
        )
        second = netboot._prepare_downloaded_netboot_assets(
            "https://example.com/release.tar.gz", "modalix", "yocto", False, "headless"
        )
        Path(first.tftp_root, "root.wic.gz").write_bytes(b"corrupt")
        third = netboot._prepare_downloaded_netboot_assets(
            "https://example.com/release.tar.gz", "modalix", "yocto", False, "headless"
        )

    assert first.reused_cache is False
    assert second.reused_cache is True
    assert third.reused_cache is False
    assert download.call_count == 2


def test_downloaded_tftp_only_assets_keep_legacy_nested_root(tmp_path):
    def fake_download(_version, _board, **kwargs):
        destination = Path(kwargs["destination_dir"])
        readme = destination / "README.txt"
        readme.write_bytes(b"metadata")
        nested = Path(kwargs["destination_dir"]) / "boot"
        nested.mkdir()
        script = nested / "netboot.scr.uimg"
        script.write_bytes(b"script")
        image = nested / "Image"
        image.write_bytes(b"kernel")
        return [str(readme), str(script), str(image)]

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path)), \
            patch.object(netboot, "download_image", side_effect=fake_download):
        assets = netboot._prepare_downloaded_netboot_assets(
            "https://example.com/minimal.tar.gz", "modalix", "elxr", False, "headless"
        )

    assert Path(assets.tftp_root).name == "boot"
    assert Path(assets.tftp_root, "Image").is_file()
    assert assets.emmc_image_paths == []


def test_mutable_version_selector_uses_resolved_source_for_cache_key(tmp_path):
    def fake_download(_version, _board, **kwargs):
        destination = Path(kwargs["destination_dir"])
        (destination / "Image").write_bytes(b"kernel")
        return [str(destination / "Image")]

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path)), \
            patch.object(netboot, "resolve_image_reference", side_effect=["3.0.0_B1", "3.0.0_B2"]), \
            patch.object(netboot, "_resolve_firmware_url", side_effect=["https://example/B1", "https://example/B2"]), \
            patch.object(netboot, "download_image", side_effect=fake_download) as download:
        first = netboot._prepare_downloaded_netboot_assets(
            "daily", "modalix", "yocto", True, "headless"
        )
        second = netboot._prepare_downloaded_netboot_assets(
            "daily", "modalix", "yocto", True, "headless"
        )

    assert first.cache_dir != second.cache_dir
    assert download.call_count == 2
    assert [call.args[0] for call in download.call_args_list] == ["3.0.0_B1", "3.0.0_B2"]


def test_internal_complete_set_selector_uses_concrete_build_for_cache_key(tmp_path):
    from sima_cli.update.netboot_artifacts import NetbootImageSelection

    selections = [
        NetbootImageSelection("3.0.0_B1", urls=("https://example/B1",)),
        NetbootImageSelection("3.0.0_B2", urls=("https://example/B2",)),
    ]

    def fake_download(selection, _board, _flavor, **kwargs):
        destination = Path(kwargs["destination_dir"])
        image = destination / "Image"
        image.write_bytes(selection.version.encode())
        return [str(image)]

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path)), \
            patch("sima_cli.update.netboot_artifacts.resolve_netboot_image_selection",
                  side_effect=selections) as resolve, \
            patch("sima_cli.update.netboot_artifacts.download_selected_netboot_image",
                  side_effect=fake_download) as download:
        first = netboot._prepare_downloaded_netboot_assets(
            "daily", "modalix", "elxr", True, "headless"
        )
        second = netboot._prepare_downloaded_netboot_assets(
            "daily", "modalix", "elxr", True, "headless"
        )

    assert first.cache_dir != second.cache_dir
    assert resolve.call_count == 2
    assert [call.args[0].version for call in download.call_args_list] == [
        "3.0.0_B1", "3.0.0_B2"
    ]


def test_cache_staging_is_removed_when_builder_exits(tmp_path):
    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path)):
        with pytest.raises(SystemExit):
            netboot._prepare_cache_entry(
                {"source": "failing"},
                lambda _content_root: (_ for _ in ()).throw(SystemExit(1)),
            )

    cache_root = tmp_path / "sima-cli" / "netboot"
    assert not list(cache_root.glob(".*"))


def test_delete_cache_removes_only_managed_entry(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    archive = images / "modalix-tftp-boot-minimal.tar.gz"
    emmc = images / "root.img.gz"
    _write_tar(archive, {"Image": b"kernel"})
    emmc.write_bytes(b"image")

    with patch.object(netboot.tempfile, "gettempdir", return_value=str(tmp_path / "tmp")):
        assets = netboot._prepare_local_netboot_assets(
            str(images), "modalix", "elxr", "headless"
        )
        netboot._delete_netboot_cache(assets.cache_dir)

    assert not Path(assets.cache_dir).exists()
    assert archive.exists()
    assert emmc.exists()


def test_setup_deletes_cache_after_server_shutdown(tmp_path):
    events = []
    tftp_root = tmp_path / "tftp"
    tftp_root.mkdir()
    (tftp_root / "netboot.scr.uimg").write_bytes(b"script")
    assets = netboot.NetbootAssets(
        tftp_root=str(tftp_root), emmc_image_paths=[],
        troot_image_path=None, cache_dir=str(tmp_path / "cache"),
    )

    with patch.object(netboot, "get_environment_type", return_value=("host", "linux")), \
            patch.object(netboot, "_prepare_netboot_assets", return_value=assets), \
            patch("sima_cli.update.netboot_device.resolve_device", return_value=None), \
            patch.object(netboot, "get_local_ip_candidates", return_value=[("eth0", "192.0.2.10")]), \
            patch.object(netboot, "ClientManager") as manager_class, \
            patch.object(netboot, "InteractiveTftpServer") as server_class, \
            patch.object(netboot.threading, "Thread") as thread_class, \
            patch.object(netboot, "run_cli"), \
            patch.object(netboot, "_acquire_cache_lease", return_value=object()), \
            patch.object(netboot, "_release_cache_lease", side_effect=lambda _lease: events.append("release")), \
            patch.object(netboot, "_delete_netboot_cache", side_effect=lambda _path: events.append("delete")):
        server_class.return_value.stop.side_effect = lambda **_kwargs: events.append("stop")
        server_class.return_value.is_running.wait.return_value = True
        manager_class.return_value.shutdown.side_effect = lambda: events.append("shutdown")
        thread_class.return_value.is_alive.return_value = False
        netboot.setup_netboot("3.0.0", "modalix", swtype="elxr", delete_cache=True)

    thread_class.return_value.join.assert_called_once_with(timeout=5)
    assert events == ["stop", "shutdown", "release", "delete"]


def test_cli_accepts_versionless_images_directory(tmp_path):
    with patch("sima_cli.update.netboot.setup_netboot") as setup:
        result = CliRunner().invoke(
            bootimg_cmd, ["--netboot", "--images", str(tmp_path)], obj={}
        )

    assert result.exit_code == 0, result.output
    assert setup.call_args.args[:4] == (None, "modalix", False, False)
    assert setup.call_args.kwargs["images"] == str(tmp_path)
    assert "delete_cache" not in setup.call_args.kwargs


@pytest.mark.parametrize(
    "args,message",
    [
        (["--netboot"], "Provide --version"),
        (["--images", "."], "--images requires"),
        (["-v", "3.0.0", "--netboot", "--images", "."], "cannot be used together"),
        (["-v", "3.0.0", "--delete-cache"], "--delete-cache requires"),
    ],
)
def test_cli_rejects_invalid_images_and_cache_combinations(args, message):
    with patch("sima_cli.update.netboot.setup_netboot") as setup, \
            patch("sima_cli.update.bootimg.write_image") as write:
        result = CliRunner().invoke(bootimg_cmd, args, obj={})

    assert result.exit_code == 2
    assert message in result.output
    setup.assert_not_called()
    write.assert_not_called()
