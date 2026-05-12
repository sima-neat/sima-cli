"""
MPK / pipeline-facing helper: deploy, kill, launch, remove, list.

NOTE:
- These are patterned after your earlier CLI shapes.
- Wire these to your actual pybind names (camelCase vs snake_case) as needed.
"""

from dataclasses import dataclass
from typing import Optional, Iterable
from tabulate import tabulate

from .api_common import BaseHelper, ensure_ok_status
from .device_api import DeviceHelper

__all__ = [
    "DeployParams",
    "KillParams",
    "LaunchParams",
    "RemoveParams",
    "MPKHelper",
]


@dataclass
class DeployParams:
    file_path: str
    target: Optional[str] = None     # Ethernet
    slot: Optional[str] = None       # PCIe
    set_default: bool = False


@dataclass
class KillParams:
    pipeline_id: Optional[str] = None
    pid: Optional[int] = None
    target: Optional[str] = None
    slot: Optional[str] = None


@dataclass
class LaunchParams:
    application: str
    target: Optional[str] = None
    slot: Optional[str] = None


@dataclass
class RemoveParams:
    application: str
    target: Optional[str] = None
    slot: Optional[str] = None


class MPKHelper(BaseHelper):
    """
    Wire these methods to your pybind functions.
    These are placeholders showing a safe call pattern with status checking.
    """

    def deploy(self, p: DeployParams):
        # Replace with your real pybind call; passing target/slot as your API expects.

        # Create device
        deviceHelper = DeviceHelper()
        deviceHandle = None
        if p.target:
            deviceHandle = deviceHelper.create_device_ethernet(p.target, "", "")
        elif p.slot:
            deviceHandle = deviceHelper.create_device_pcie(p.slot)

        deploy_fn = getattr(self._helper, "deployApp", None) or getattr(
            self._helper, "deploy", None
        )
        if not deploy_fn:
            raise RuntimeError("Pybind helper does not expose deploy method.")

        # Connect device
        status = deviceHelper.connect_device(deviceHandle)

        # Typical signature might differ; adapt params as necessary:
        print("\n------------- Deploy ---------------")
        appInfo = self._pb.ApplicationInfo()
        status = deploy_fn(deviceHandle.raw, p.file_path, p.set_default, appInfo)
        ensure_ok_status("Deploy", status, context=p.target or p.slot)

        # Display launched pipeline info
        print(f"\nListing Deployed pipeline on Device: {deviceHandle.target}")
        headers = ["mpk_id", "instance_id", "pid", "Application ID", "Target", "Status"]
        tableData = [[appInfo.getMpkId(), appInfo.getInstanceName(), appInfo.getPidStr(), appInfo.getApplicationName(), deviceHandle.target, str(appInfo.getApplicationState())]]
        print(tabulate(tableData, headers=headers, tablefmt="grid"))

        return True

    def kill(self, p: KillParams):
        if not p.pipeline_id and not p.pid:
            raise ValueError("Provide either pipeline_id or pid.")

        kill_by_id_fn = getattr(self._helper, "killAppByPipelineId", None)
        kill_by_pid_fn = getattr(self._helper, "killAppByPid", None)

        if p.pipeline_id and not kill_by_id_fn:
            raise RuntimeError("Pybind helper does not expose kill by pipeline_id.")

        if p.pid and not kill_by_pid_fn:
            raise RuntimeError("Pybind helper does not expose kill by pid.")

        # Create device
        deviceHelper = DeviceHelper()
        deviceHandle = None
        if p.target:
            deviceHandle = deviceHelper.create_device_ethernet(p.target, "", "")
        elif p.slot:
            deviceHandle = deviceHelper.create_device_pcie(p.slot)

        # Connect device
        status = deviceHelper.connect_device(deviceHandle)

        # Call the kill api to kill pipeline
        print(f"Sending Kill request to device: {deviceHandle.target}")
        killedAppInfoList = self._pb.VectorApplicationInfo([])
        if p.pid:
            killedAppInfo = self._pb.ApplicationInfo()
            status = kill_by_pid_fn(deviceHandle.raw, p.pid, killedAppInfo)
            ensure_ok_status("Kill", status, context=f"pid={p.pid}")
            killedAppInfoList.append(killedAppInfo)

        elif p.pipeline_id:
            status = kill_by_id_fn(deviceHandle.raw, p.pipeline_id, killedAppInfoList)
            ensure_ok_status("Kill", status, context=f"pipeline_id={p.pipeline_id}")
        else:
            raise RuntimeError("Neither pipeline_id nor pid is specified.")

        # Display killed pipelines info
        print(f"\nListing killed pipelines on Device: {deviceHandle.target}")
        headers = ["mpk_id", "instance_id", "pid", "Application ID", "Target", "Status"]
        tableData = [[appInfo.getMpkId(), appInfo.getInstanceName(), appInfo.getPidStr(), appInfo.getApplicationName(), deviceHandle.target, str(appInfo.getApplicationState())] for appInfo in killedAppInfoList]
        print(tabulate(tableData, headers=headers, tablefmt="grid"))
        return True

    def launch(self, p: LaunchParams):
        # Create device
        deviceHelper = DeviceHelper()
        deviceHandle = None
        if p.target:
            deviceHandle = deviceHelper.create_device_ethernet(p.target, "", "")
        elif p.slot:
            deviceHandle = deviceHelper.create_device_pcie(p.slot)

        launch_fn = getattr(self._helper, "launchApp", None)
        if not launch_fn:
            raise RuntimeError("Pybind helper does not expose a launch method.")

        # Connect device
        status = deviceHelper.connect_device(deviceHandle)

        # Launch Pipeline
        print("\n------------- Launch ---------------")
        appInfo = self._pb.ApplicationInfo()
        status = launch_fn(deviceHandle.raw, p.application, appInfo)
        ensure_ok_status("Launch", status, context=p.target or p.slot)

        # Display launched pipeline info
        print(f"\nListing Launched pipeline on Device: {deviceHandle.target}")
        headers = ["mpk_id", "instance_id", "pid", "Application ID", "Target", "Status"]
        tableData = [[appInfo.getMpkId(), appInfo.getInstanceName(), appInfo.getPidStr(), appInfo.getApplicationName(), deviceHandle.target, str(appInfo.getApplicationState())]]
        print(tabulate(tableData, headers=headers, tablefmt="grid"))
        return True

    def remove(self, p: RemoveParams):
        # Create device
        deviceHelper = DeviceHelper()
        deviceHandle = None
        if p.target:
            deviceHandle = deviceHelper.create_device_ethernet(p.target, "", "")
        elif p.slot:
            deviceHandle = deviceHelper.create_device_pcie(p.slot)

        remove_fn = getattr(self._helper, "removeApp", None)
        if not remove_fn:
            raise RuntimeError("Pybind helper does not expose a remove method.")

        # Connect device
        status = deviceHelper.connect_device(deviceHandle)

        # Typical signature might differ; adapt params as necessary:
        print("\n------------- Remove ---------------")
        appInfo = self._pb.ApplicationInfo()
        status = remove_fn(deviceHandle.raw, p.application)
        ensure_ok_status("Remove", status, context=p.target or p.slot)
        return True

    def list(self, where: Optional[str] = None):
        """
        Return a typed list if your pybind returns application info.
        For now, return a list of dicts as a placeholder.
        """
        # Fetch the api function
        list_fn = getattr(self._helper, "listApps", None)
        if not list_fn:
            raise RuntimeError("Pybind helper does not expose a list method.")

        # Fetch all the devices
        deviceHelper = DeviceHelper()
        deviceHandleList = deviceHelper.list_devices()

        # Iterate through all devices and connect and fetch deployed pipelines info
        for deviceHandle in deviceHandleList:
            try:
                # Connect device
                status = deviceHelper.connect_device(deviceHandle)

                # Call the list api
                appInfoList = self._pb.VectorApplicationInfo([])
                status = list_fn(deviceHandle.raw, appInfoList)
                ensure_ok_status("list", status, context=deviceHandle.target)

                # Display all deployed pipelines info
                print(f"\nListing pipelines deployed on Device: {deviceHandle.target}")
                headers = ["mpk_id", "instance_id", "pid", "Application ID", "Target", "Status"]
                tableData = [[appInfo.getMpkId(), appInfo.getInstanceName(), appInfo.getPidStr(), appInfo.getApplicationName(), deviceHandle.target, str(appInfo.getApplicationState())] for appInfo in appInfoList]
                print(tabulate(tableData, headers=headers, tablefmt="grid"))
            except Exception as e:
                print(f"Error fetching details for Device: {deviceHandle.raw.getTarget()}, Exception: {str(e)}")
