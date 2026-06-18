import unittest
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sima_cli.sdk import linux_devkit_network as net


class TestLinuxDevkitNetwork(unittest.TestCase):
    def test_parse_ip_route_get_extracts_interface_and_source(self):
        iface, source = net.parse_ip_route_get(
            "10.0.0.244 dev enp3s0 src 10.0.0.210 uid 1000 cache"
        )

        self.assertEqual(iface, "enp3s0")
        self.assertEqual(source, "10.0.0.210")

    def test_classify_interface_detects_vpn_and_virtual(self):
        self.assertEqual(net.classify_interface("wg0"), "vpn")
        self.assertEqual(net.classify_interface("tailscale0"), "vpn")
        self.assertEqual(net.classify_interface("docker0"), "virtual")
        self.assertEqual(net.classify_interface("br-abc123"), "virtual")
        self.assertEqual(net.classify_interface("enp3s0"), "physical")

    def test_port_specs_from_port_map_uses_generated_ports(self):
        port_map = {
            "mainUI": {"protocol": "tcp", "host": 19900, "container": 9900},
            "videoUI": {"protocol": "tcp", "host": 18081, "container": 8081},
            "rtsp": {"tcp": {"host": 18554, "container": 8554}},
            "videoUDP": {
                "protocol": "udp",
                "hostStart": 19000,
                "hostEnd": 19001,
                "containerStart": 9000,
                "containerEnd": 9001,
            },
        }

        specs = net.port_specs_from_port_map(port_map)

        self.assertIn(net.PortSpec("mainUI", "tcp", 19900, 19900, 9900, 9900), specs)
        self.assertIn(net.PortSpec("videoUI", "tcp", 18081, 18081, 8081, 8081), specs)
        self.assertIn(net.PortSpec("rtsp.tcp", "tcp", 18554, 18554, 8554, 8554), specs)
        self.assertIn(net.PortSpec("videoUDP", "udp", 19000, 19001, 9000, 9001), specs)

    def test_detects_port_map_publication_mismatch(self):
        inspect = {
            "NetworkSettings": {
                "Ports": {
                    "9900/tcp": [{"HostPort": "19900"}],
                    "8081/tcp": [{"HostPort": "28081"}],
                }
            }
        }
        specs = [
            net.PortSpec("mainUI", "tcp", 19900, 19900, 9900, 9900),
            net.PortSpec("videoUI", "tcp", 18081, 18081, 8081, 8081),
        ]

        mismatches = net._missing_or_mismatched_port_publications(inspect, specs)

        self.assertEqual(len(mismatches), 1)
        self.assertIn("videoUI", mismatches[0])
        self.assertIn("18081->8081/tcp", mismatches[0])

    def test_stale_saved_port_binding_conflicts_ignore_running_container(self):
        inspect = {
            "State": {"Running": True},
            "HostConfig": {
                "PortBindings": {
                    "9900/tcp": [{"HostPort": "19900"}],
                }
            },
        }

        with patch.object(net, "_can_bind", return_value=False):
            self.assertEqual(net.stale_saved_port_binding_conflicts(inspect), [])

    def test_stale_saved_port_binding_conflicts_detect_occupied_stopped_ports(self):
        inspect = {
            "State": {"Running": False},
            "HostConfig": {
                "PortBindings": {
                    "9900/tcp": [{"HostPort": "19900"}],
                }
            },
        }

        with patch.object(net, "_can_bind", return_value=False):
            self.assertEqual(net.stale_saved_port_binding_conflicts(inspect), ["19900:9900/tcp"])

    def test_report_blocks_vpn_route(self):
        route = net.RouteProbe(
            target_ip="10.0.0.244",
            interface="wg0",
            source_ip="10.9.0.2",
            raw="10.0.0.244 dev wg0 src 10.9.0.2",
            classification="vpn",
        )

        with patch.object(net, "_is_linux_host", return_value=True), \
             patch.object(net, "probe_route_to_devkit", return_value=route), \
             patch.object(net, "resolve_neat_sdk_container", return_value=("", "No Neat SDK containers were found.")):
            report = net.build_network_doctor_report(devkit_ip="10.0.0.244")

        self.assertTrue(report.has_errors)
        self.assertTrue(any(f.code == "vpn-route" for f in report.findings))

    def test_container_default_route_confirmed_from_docker_gateway(self):
        inspect = {
            "NetworkSettings": {
                "Networks": {
                    "simasdkbridge": {
                        "Gateway": "172.18.0.1",
                    }
                }
            }
        }

        with patch.object(net, "_docker_exec_success") as docker_exec_success:
            self.assertTrue(net._container_default_route_confirmed("sdk", inspect))

        docker_exec_success.assert_not_called()

    def test_sanitize_json_redacts_secret_like_values(self):
        sanitized = net._sanitize_json({
            "Config": {
                "Env": [
                    "HUGGINGFACE_TOKEN=abc123",
                    "NORMAL=value",
                ],
                "Labels": {
                    "auth_token": "abc123",
                    "safe": "value",
                },
            }
        })

        self.assertEqual(sanitized["Config"]["Env"][0], "HUGGINGFACE_TOKEN=<redacted>")
        self.assertEqual(sanitized["Config"]["Env"][1], "NORMAL=value")
        self.assertEqual(sanitized["Config"]["Labels"]["auth_token"], "<redacted>")
        self.assertEqual(sanitized["Config"]["Labels"]["safe"], "value")

    def test_collect_network_doctor_bundle_creates_sanitized_tarball(self):
        report = net.NetworkDoctorReport(container="", devkit_ip="10.0.0.244")
        report.add("warning", "test-warning", "A test warning")

        with TemporaryDirectory() as tmpdir, \
             patch.object(net, "_is_linux_host", return_value=False), \
             patch.object(net, "_collect_docker_state") as collect_docker_state:
            bundle = net.collect_network_doctor_bundle(
                report,
                output_path=str(Path(tmpdir) / "doctor.tar.gz"),
            )

            self.assertTrue(Path(bundle).exists())
            collect_docker_state.assert_called_once()
            with tarfile.open(bundle, "r:gz") as archive:
                names = archive.getnames()
                self.assertIn("sima-sdk-network-doctor/summary.json", names)
                self.assertIn("sima-sdk-network-doctor/doctor-report.json", names)
                self.assertIn("sima-sdk-network-doctor/README.txt", names)
                summary = archive.extractfile("sima-sdk-network-doctor/summary.json").read().decode("utf-8")

        self.assertIn("test-warning", summary)

    def test_collect_json_command_writes_parseable_json(self):
        with TemporaryDirectory() as tmpdir, \
             patch.object(
                 net,
                 "_run_captured",
                 return_value=net.CommandResult(0, '[{"Name":"simasdkbridge","Options":{"token":"secret"}}]\n', ""),
             ):
            written = net._collect_json_command(
                Path(tmpdir),
                "docker/network-simasdkbridge.json",
                ["docker", "network", "inspect", "simasdkbridge"],
            )
            content = (Path(tmpdir) / "docker/network-simasdkbridge.json").read_text(encoding="utf-8")

        self.assertTrue(written)
        self.assertIn('"Name": "simasdkbridge"', content)
        self.assertIn('"token": "<redacted>"', content)
        self.assertNotIn("$ docker network inspect", content)

    def test_report_flags_nm_shared_iptables_blocking_before_container_resolution(self):
        route = net.RouteProbe(
            target_ip="10.42.0.78",
            interface="eno1",
            source_ip="10.42.0.1",
            raw="10.42.0.78 dev eno1 src 10.42.0.1",
            classification="physical",
        )
        status = {
            "applicable": True,
            "rule_present": False,
            "dispatcher_installed": False,
            "chain": "nm-sh-fw-eno1",
            "devkit_iface": "eno1",
            "devkit_subnet": "10.42.0.0/24",
            "docker_subnet": "172.19.0.0/16",
        }

        with patch.object(net, "_is_linux_host", return_value=True), \
             patch.object(net, "probe_route_to_devkit", return_value=route), \
             patch.object(net, "nm_shared_iptables_repair_status", return_value=status) as repair_status, \
             patch.object(net, "resolve_neat_sdk_container", return_value=("", "No Neat SDK containers were found.")):
            report = net.build_network_doctor_report(devkit_ip="10.42.0.78")

        repair_status.assert_called_once_with("10.42.0.78", allow_sudo_prompt=False)
        self.assertTrue(any(f.code == "nm-shared-iptables-blocking" for f in report.findings))
        self.assertTrue(any("--persist" in f.detail for f in report.findings))

    def test_repair_linux_devkit_network_forwards_persist_flag(self):
        report = net.NetworkDoctorReport(devkit_ip="10.42.0.78")
        with patch.object(net, "build_network_doctor_report", return_value=report), \
             patch.object(net, "_is_linux_host", return_value=True), \
             patch.object(net, "configure_linux_shared_devkit_network") as configure:
            repaired = net.repair_linux_devkit_network(devkit_ip="10.42.0.78", persist=True)

        configure.assert_called_once_with("10.42.0.78", persist=True)
        self.assertTrue(any(f.code == "shared-network-repair" for f in repaired.findings))


if __name__ == "__main__":
    unittest.main()
