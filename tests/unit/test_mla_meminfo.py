import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from sima_cli.cli import show_mla_memory_usage
from sima_cli.mla.meminfo import (
    DMA_BUFINFO_PATH,
    LEGACY_MEMORY_PATH,
    MLA_DMA_HEAP_PATH,
    MemorySource,
    MemoryTelemetryError,
    chart_limits_mib,
    detect_memory_source,
    parse_dma_buf_mla_bytes,
    parse_dma_buf_objects,
    parse_legacy_allocated_bytes,
    read_mla_memory_bytes,
)


DMA_BUF_FIXTURE = """
Dma-buf Objects:
size    \tflags   \tmode    \tcount   \texp_name\tino     \tname
00004096\t00000002\t02080007\t00000002\tlinux,cma\t00000014\t<none>
\tAttached Devices:
Total 0 devices attached

02097152\t00000002\t02080007\t00000002\tsimaai,dms\t00000013\tweights
\tAttached Devices:
\t5000000.mla
Total 1 devices attached

01048576\t00000002\t02080007\t00000002\tlinux,cma\t00000004\tinput
\tAttached Devices:
\t5000000.mla
Total 1 devices attached

00524288\t00000002\t02080007\t00000002\tlinux,cma\t00000003\tcamera
\tAttached Devices:
\t5100000.cvu
Total 1 devices attached

Total 4 objects, 3674112 bytes
"""


class TestMlaMeminfo(unittest.TestCase):
    def test_parse_legacy_allocated_bytes_accepts_zero(self):
        output = "| Total buffers allocated: 0 | Total allocated size: 0x0000000000 |\n\0"
        self.assertEqual(parse_legacy_allocated_bytes(output), 0)

    def test_parse_legacy_allocated_bytes_accepts_live_2_1_3_shape(self):
        output = "| Total buffers allocated: 8 | Total allocated size: 0x0000301044 |"
        self.assertEqual(parse_legacy_allocated_bytes(output), 0x301044)

    def test_parse_legacy_allocated_bytes_rejects_missing_total(self):
        with self.assertRaisesRegex(ValueError, "missing legacy"):
            parse_legacy_allocated_bytes("not allocator output")

    def test_parse_dma_buf_objects_tracks_exporter_inode_and_attachments(self):
        objects = parse_dma_buf_objects(DMA_BUF_FIXTURE)
        self.assertEqual(len(objects), 4)
        self.assertEqual(objects[1].size_bytes, 2097152)
        self.assertEqual(objects[1].exporter, "simaai,dms")
        self.assertEqual(objects[1].inode, 13)
        self.assertEqual(objects[1].attached_devices, ("5000000.mla",))

    def test_dma_buf_accounting_uses_union_without_double_counting(self):
        self.assertEqual(parse_dma_buf_mla_bytes(DMA_BUF_FIXTURE), 2097152 + 1048576)

    def test_dma_buf_accounting_accepts_transitional_mla_exporter(self):
        fixture = DMA_BUF_FIXTURE.replace("simaai,dms", "mla")
        self.assertEqual(parse_dma_buf_mla_bytes(fixture), 2097152 + 1048576)

    def test_dma_buf_accounting_accepts_idle_snapshot(self):
        fixture = """
Dma-buf Objects:
size    flags    mode    count   exp_name    ino     name

Total 0 objects, 0 bytes
"""
        self.assertEqual(parse_dma_buf_mla_bytes(fixture), 0)

    def test_dma_buf_parser_rejects_non_bufinfo_text(self):
        with self.assertRaisesRegex(ValueError, "missing DMA-BUF"):
            parse_dma_buf_objects("permission denied")

    def test_dma_buf_parser_rejects_unparsed_objects(self):
        fixture = """
Dma-buf Objects:
size    flags    mode    count   exp_name    ino     name
truncated object row

Total 1 objects, 4096 bytes
"""
        with self.assertRaisesRegex(ValueError, "object count mismatch"):
            parse_dma_buf_objects(fixture)

    def test_dma_buf_parser_rejects_missing_summary(self):
        fixture = """
Dma-buf Objects:
size    flags    mode    count   exp_name    ino     name
00004096 00000002 02080007 00000002 mla 00000014 weights
"""
        with self.assertRaisesRegex(ValueError, "object summary"):
            parse_dma_buf_objects(fixture)

    def test_detects_dma_buf_backend_from_3_0_heap(self):
        existing = {MLA_DMA_HEAP_PATH, LEGACY_MEMORY_PATH}
        source = detect_memory_source(existing.__contains__)
        self.assertEqual(source.path, DMA_BUFINFO_PATH)

    def test_detects_legacy_backend_when_dma_heap_is_absent(self):
        source = detect_memory_source({LEGACY_MEMORY_PATH}.__contains__)
        self.assertEqual(source.path, LEGACY_MEMORY_PATH)

    def test_missing_backends_are_actionable(self):
        with self.assertRaisesRegex(MemoryTelemetryError, "No supported"):
            detect_memory_source(lambda _path: False)

    def test_selected_dma_backend_does_not_fall_back_to_legacy(self):
        source = MemorySource("DMA-BUF", DMA_BUFINFO_PATH, parse_dma_buf_mla_bytes)
        failure = subprocess.CompletedProcess(
            ["sudo", "cat", DMA_BUFINFO_PATH], 1, "", "debugfs is not mounted"
        )
        with patch(
            "sima_cli.mla.meminfo.open", side_effect=PermissionError
        ), patch("sima_cli.mla.meminfo.subprocess.run", return_value=failure):
            with self.assertRaisesRegex(MemoryTelemetryError, "debugfs is not mounted"):
                read_mla_memory_bytes(source)

    def test_zero_chart_range_is_nonzero(self):
        self.assertEqual(chart_limits_mib([0.0]), (0.0, 1.0))

    def test_positive_constant_chart_range_is_nonzero(self):
        low, high = chart_limits_mib([128.0, 128.0])
        self.assertLess(low, 128.0)
        self.assertGreater(high, 128.0)

    def test_varying_chart_range_includes_all_samples(self):
        low, high = chart_limits_mib([10.0, 20.0])
        self.assertLessEqual(low, 10.0)
        self.assertGreaterEqual(high, 20.0)

    def test_click_command_exits_nonzero_on_telemetry_failure(self):
        failure = MemoryTelemetryError("debugfs telemetry is unavailable")
        with patch(
            "sima_cli.cli.monitor_simaai_mem_chart", side_effect=failure
        ):
            result = CliRunner().invoke(show_mla_memory_usage)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: debugfs telemetry is unavailable", result.output)


if __name__ == "__main__":
    unittest.main()
