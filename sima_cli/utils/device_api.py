"""
Device-facing helper: creation, connection, lifecycle operations.
Relies on api_common for shared logic.

This version completely removes default model handling.
The `model` field is kept internally as an empty string for compatibility.
"""

from dataclasses import dataclass
from typing import Optional, List
from .api_common import BaseHelper, DeviceHandle, ensure_ok_status

__all__ = [
    "DeviceCreateParams",
    "FirmwareUpgradeParams",
    "DeviceHelper",
]


@dataclass
class DeviceCreateParams:
    # Ethernet
    target: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    # PCIe
    slot: Optional[str] = None

@dataclass
class FirmwareUpgradeParams:
    file_path: str
    target: Optional[str] = None     # Ethernet
    slot: Optional[str] = None       # PCIe
    reboot_on_upgrade: bool = False

class DeviceHelper(BaseHelper):
    DEFAULT_USER = "sima"
    DEFAULT_PASSWORD = "edgeai"

    def _default_model_enum(self):
        """Return a valid Device_Model enum required by pybind (use DAVINCI by default)."""
        try:
            return getattr(self._pb, "DAVINCI")
        except AttributeError as e:
            # If your binding uses a different name, adjust here (e.g., self._pb.DeviceModel.DAVINCI)
            raise RuntimeError(
                "Pybind requires a Device_Model enum. 'DAVINCI' was not found. "
                "Update _default_model_enum() to return the correct enum for your build."
            ) from e

    # Ethernet
    def create_device_ethernet(
        self,
        target: str,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> DeviceHandle:
        if not target:
            raise ValueError("target (IP/FQDN) is required for Ethernet device creation.")
        user = user or self.DEFAULT_USER
        password = password or self.DEFAULT_PASSWORD

        create_eth = getattr(self._helper, "createDeviceEthernet", None) or getattr(
            self._helper, "create_device_ethernet", None
        )
        if not create_eth:
            raise RuntimeError(
                "Pybind helper does not expose an Ethernet create method "
                "(createDeviceEthernet/create_device_ethernet)."
            )

        model_enum = self._default_model_enum()  # <-- use enum, not ""
        dev = create_eth(model_enum, target, user, password)
        if dev is None:
            raise RuntimeError("createDeviceEthernet returned None (device not created).")
        return DeviceHandle(raw=dev, kind=str(dev.getConnectionMode()), model=str(dev.getModel()), target=dev.getTarget())

    # PCIe
    def create_device_pcie(self, slot: str) -> DeviceHandle:
        if not slot:
            raise ValueError("slot is required for PCIe device creation.")
        create_pcie = getattr(self._helper, "createDevicePCIe", None) or getattr(
            self._helper, "create_device_pcie", None
        )
        if not create_pcie:
            raise RuntimeError(
                "Pybind helper does not expose a PCIe create method "
                "(createDevicePCIe/create_device_pcie)."
            )

        model_enum = self._default_model_enum()  # <-- use enum, not ""
        dev = create_pcie(model_enum, str(slot))
        if dev is None:
            raise RuntimeError("createDevicePCIe returned None (device not created).")
        return DeviceHandle(raw=dev, kind=str(dev.getConnectionMode()), model=str(dev.getModel()), target=dev.getTarget())
    
    # -----------------------------
    # Connect / Disconnect
    # -----------------------------
    def connect_device(self, device: DeviceHandle):
        """
        Connect to a created device (Ethernet or PCIe), with verbose logs
        matching the reference snippets.
        """
        connect = getattr(self._helper, "connectDevice", None) or getattr(
            self._helper, "connect_device", None
        )
        if not connect:
            raise RuntimeError("Pybind helper does not expose connectDevice/connect_device.")

        # Try to fetch a printable target
        target_str = None
        try:
            if hasattr(device.raw, "getTarget"):
                target_str = device.raw.getTarget()
        except Exception:
            target_str = None
        if not target_str:
            target_str = device.target or device.slot or "<unknown>"

        print("\n------------- Connect ---------------")
        print("Connecting to a SiMa.ai Device:", target_str)

        status = connect(device.raw)
        if hasattr(status, "isError") and callable(status.isError) and status.isError():
            err = status.ToString() if hasattr(status, "ToString") else "Unknown error"
            if device.kind == "DeviceConnectionMode.PCIE":
                raise RuntimeError(f"Error Connecting to PCIe device. Error: {err}")
            raise RuntimeError(f"Error Connecting to Ethernet device. Error: {err}")
        else:
            msg = ""
            if status.getCode() == self._pb.SiMaCoProcessingAPIErrorCode.CP_ERR_DEVICE_ALREADY_MAPPED.value:
                msg = "[Device Already Mapped]"
            if device.kind == "DeviceConnectionMode.PCIE":
                print(f"Successfully connected to PCIe Device: {target_str} {msg}")
            else:
                print(f"Successfully connected to Ethernet Device: {target_str} {msg}")

        return True

    def disconnect_device(self, device: DeviceHandle):
        """
        Disconnect from a created device (Ethernet or PCIe).
        """
        disconnect = getattr(self._helper, "disconnectDevice", None) or getattr(
            self._helper, "disconnect_device", None
        )
        if not disconnect:
            raise RuntimeError("Pybind helper does not expose disconnectDevice/disconnect_device.")

        target_str = None
        try:
            if hasattr(device.raw, "getTarget"):
                target_str = device.raw.getTarget()
        except Exception:
            target_str = None
        if not target_str:
            target_str = device.target or "<unknown>"

        print("\n------------- Disconnect ---------------")
        print(f"Disconnecting SiMa.ai Device: {target_str}")

        status = disconnect(device.raw)
        if hasattr(status, "isError") and callable(status.isError) and status.isError():
            err_msg = status.ToString() if hasattr(status, "ToString") else "Unknown error"
            raise RuntimeError(f"Error Disconnecting device. Error: {err_msg}")

        if device.kind == "DeviceConnectionMode.PCIE":
            print(f"Successfully Disconnected from PCIe Device: {target_str}")
        else:
            print(f"Successfully Disconnected from Ethernet Device: {target_str}")

        return True

    # -----------------------------
    # Reboot / Reset
    # -----------------------------
    def reboot_device(self, device: DeviceHandle):
        reboot = getattr(self._helper, "rebootDevice", None) or getattr(
            self._helper, "reboot_device", None
        )
        if not reboot:
            raise RuntimeError("Pybind helper does not expose rebootDevice/reboot_device.")

        target_str = None
        try:
            if hasattr(device.raw, "getTarget"):
                target_str = device.raw.getTarget()
        except Exception:
            target_str = None
        if not target_str:
            target_str = device.target or device.slot or "<unknown>"

        print("\n------------- Reboot Device ---------------")
        print(f"Trying to reboot SiMa.ai Device: {target_str}")

        status = reboot(device.raw)
        if hasattr(status, "isError") and callable(status.isError) and status.isError():
            err_msg = status.ToString() if hasattr(status, "ToString") else "Unknown error"
            raise RuntimeError(f"Error Rebooting device. Error: {err_msg}")

        print(f"Successfully Rebooted SiMa.ai Device: {target_str}")
        return True

    def reset_device(self, device: DeviceHandle):
        reset = getattr(self._helper, "resetDevice", None) or getattr(
            self._helper, "reset_device", None
        )
        if not reset:
            raise RuntimeError("Pybind helper does not expose resetDevice/reset_device.")
        status = reset(device.raw)
        ensure_ok_status("Reset", status, context=device.target or device.slot)
        return True

    def device_firmware_upgrade(self, device: DeviceHandle, file_path: str, reboot_on_upgrade : bool):
        firmware_upgrade_fn = getattr(self._helper, "deviceFirmwareUpgrade", None)
        if not firmware_upgrade_fn:
            raise RuntimeError("Pybind helper does not expose firmware upgrade support for device.")
        status = firmware_upgrade_fn(device.raw, file_path, reboot_on_upgrade)
        ensure_ok_status("FirmwareUpgrade", status, context=device.target or device.slot)
        return True

    # -----------------------------
    # List Connected Devices
    # -----------------------------
    # def list_devices(self) -> list[str]:
    def list_devices(self) -> List[DeviceHandle]:
        """
        Fetch and return all connected devices using the pybind API.
        """
        vector_device = self._pb.VectorDevice([])

        list_fn = getattr(self._helper, "listDevices", None)
        if not list_fn:
            raise RuntimeError("Pybind helper does not expose listDevices().")

        status = list_fn(vector_device)
        ensure_ok_status("List Devices", status)

        # devices = []
        # for index, dev in enumerate(vector_device, start=1):
        #     devices.append(f"{index}: {dev.toString()}")
        # return devices

        deviceHandleList = []
        for device in vector_device:
            #(raw=dev, kind="pcie", model="DAVINCI", slot=str(slot))
            print(f"Found available Device: {device.toString()} to Devices list")
            deviceHandleList.append(DeviceHandle(raw=device, kind=str(device.getConnectionMode()), model=str(device.getModel()), target=device.getTarget()))
        return deviceHandleList
    # -----------------------------
    # High-level convenience
    # -----------------------------
    def create_device(self, params: DeviceCreateParams) -> DeviceHandle:
        if params.target:
            return self.create_device_ethernet(
                target=params.target, user=params.user, password=params.password
            )
        if params.slot:
            return self.create_device_pcie(slot=params.slot)
        raise ValueError("Provide either 'target' (Ethernet) or 'slot' (PCIe).")

    def create_and_connect_device(self, params: DeviceCreateParams) -> DeviceHandle:
        device = self.create_device(params)
        self.connect_device(device)
        return device