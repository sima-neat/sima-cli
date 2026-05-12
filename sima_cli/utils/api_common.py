"""
Shared utilities for pybind integration:
- Friendly import with guidance if missing/broken
- Model enum resolution
- BaseHelper that loads the pybind module and helper instance
- Status checking helper
"""

from dataclasses import dataclass
from typing import Any, Optional


__all__ = [
    "_ImportErrorWithHint",
    "import_pybind_or_raise",
    "resolve_model_enum",
    "BaseHelper",
    "ensure_ok_status",
    "DeviceHandle",
]


class _ImportErrorWithHint(ImportError):
    """Raised when the pybind module can't be imported, with guidance for the user."""
    pass


def import_pybind_or_raise():
    """Import the pybind module with clear, actionable error message."""
    try:
        import simaai_coprocessing_apis  # noqa: F401
        return simaai_coprocessing_apis
    except Exception as e:
        raise _ImportErrorWithHint(
            "\n❌ Failed to import the required pybind module 'simaai_coprocessing_apis'.\n\n"
            "This usually means the CoProcessing package is either:\n"
            "  1) Not installed in your current environment, OR\n"
            "  2) Installed but broken (wrong Python version, missing dependencies, etc.)\n\n"
            "To fix this, follow these steps:\n"
            "  ➤ Download the CoProcessing package:\n"
            "      sima-cli download coprocessing_package\n\n"
            "  ➤ Install the downloaded package:\n"
            "      sima-cli install <path-to-downloaded-package>\n\n"
            "If the issue persists, please ensure you're running inside a compatible environment."
        ) from e


def resolve_model_enum(pb_mod, model_name: str):
    """Convert e.g. 'davinci' -> simaai_coprocessing_apis.DAVINCI."""
    if not model_name:
        raise ValueError("device_model is required.")
    enum_name = model_name.upper()
    try:
        return getattr(pb_mod, enum_name)
    except AttributeError as e:
        raise ValueError(
            f"Unsupported device model '{model_name}'. "
            "Expected an enum exposed by simaai_coprocessing_apis."
        ) from e


def ensure_ok_status(action: str, status: Any, context: str = ""):
    """
    Common status checker. Expects pybind status objects to expose
    isError() and ToString(). No-op if such methods don't exist.
    """
    if hasattr(status, "isError") and callable(status.isError) and status.isError():
        err_str = status.ToString() if hasattr(status, "ToString") else "Unknown error"
        prefix = f"{action} failed"
        raise RuntimeError(f"{prefix}{(' - ' + context) if context else ''}: {err_str}")


@dataclass
class DeviceHandle:
    """
    Thin wrapper around the pybind device pointer so callers don’t depend on
    pybind types directly.
    """
    raw: Any
    kind: str  # "ethernet" or "pcie"
    model: str
    target: Optional[str] = None


class BaseHelper:
    """
    Loads the pybind module and constructs CoProcessingApisHelper.
    Subclasses can access:
      - self._pb       (the pybind module)
      - self._helper   (instance of CoProcessingApisHelper)
    """

    def __init__(self):
        self._pb = import_pybind_or_raise()
        try:
            self._helper = self._pb.CoProcessingApisHelper()
        except Exception as e:
            raise RuntimeError(
                "Failed to instantiate CoProcessingApisHelper. "
                "Ensure the CoProcessing package is correctly installed and functional."
            ) from e