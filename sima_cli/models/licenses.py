from typing import Any, Optional


# Model Browser's artifact-browser/licenses.json on Models main, 0989e0a6d022.
# Keep this snapshot until the registry publishes the Browser's decision with each catalog model.
_DOWNLOADABLE_LICENSES = frozenset({
    "AGPL-3.0-only",
    "Apache-2.0",
    "BSD-3-Clause",
    "GPL-3.0-only",
    "LicenseRef-BigScience-OpenRAIL-M",
    "LicenseRef-DINOv3",
    "LicenseRef-LFM-Open-1.0",
    "LicenseRef-Llama-3.1-Community",
    "LicenseRef-Llama-3.2-Community",
    "LicenseRef-NVIDIA-Source-Code-License-SegFormer",
    "LicenseRef-Swallow-Llama-Gemma",
    "MIT",
})
_BLOCKED_LICENSES = frozenset({
    "LicenseRef-MagicLeap-Noncommercial-Research",
    "LicenseRef-OpenStereo-Academic",
})


def download_block_reason(license: Any) -> Optional[str]:
    spdx = license.get("spdx") if isinstance(license, dict) else None
    if not isinstance(spdx, str) or not spdx:
        return "The catalog does not identify the model's license."
    if spdx in _BLOCKED_LICENSES:
        return f"The {spdx} license does not permit redistribution."
    if spdx not in _DOWNLOADABLE_LICENSES:
        return f"The Model Browser download policy does not list {spdx} as downloadable."
    return None
