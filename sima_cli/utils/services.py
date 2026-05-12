"""
Tiny service locator for your CLI: import once, reuse across commands.
"""

from dataclasses import dataclass

from .device_api import DeviceHelper
from .mpk_api import MPKHelper


__all__ = ["Services", "services"]


@dataclass
class Services:
    device: DeviceHelper
    mpk: MPKHelper


def services() -> Services:
    return Services(
        device=DeviceHelper(),
        mpk=MPKHelper(),
    )