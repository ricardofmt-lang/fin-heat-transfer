"""Numerical JSON preset format at the app/solver boundary.

The public file calls fin segments ``sections``.  Existing saved files and
the numerical solver call the same list ``domains``.  These conversions only
rename that top-level key; they neither supply values nor change units.
"""

from __future__ import annotations

import copy
from typing import Any


_OTHER_FIELDS = ("controls", "geometry", "temperature", "graphs")


def _check_preset(config: Any, *, internal: bool) -> str:
    if not isinstance(config, dict):
        raise ValueError("Numerical inputs JSON must be an object.")
    has_sections = "sections" in config
    has_domains = "domains" in config
    if has_sections and has_domains:
        raise ValueError("Numerical inputs JSON cannot contain both sections and domains.")
    if not has_sections and not has_domains:
        raise ValueError("Numerical inputs JSON needs a sections list (legacy domains is also accepted).")
    if internal and not has_domains:
        raise ValueError("Internal numerical inputs must contain domains.")

    for key in _OTHER_FIELDS:
        if not isinstance(config.get(key), dict):
            raise ValueError(f"Numerical inputs JSON needs a {key} object.")
    list_key = "domains" if has_domains else "sections"
    entries = config[list_key]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Numerical inputs JSON needs a nonempty {list_key} list.")
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError(f"Every {list_key} entry must be an object.")
    return list_key


def export_numerical_preset(config: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready copy of an internal config with ``sections``.

    This accepts the same five top-level fields as ``simulate``.  ``None``
    values representing empty form inputs remain ``None`` (JSON ``null``).
    """

    _check_preset(config, internal=True)
    exported = copy.deepcopy(config)
    exported["sections"] = exported.pop("domains")
    return exported


def import_numerical_preset(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an internal config from a current or legacy numerical preset.

    Current presets have ``sections``; older files with ``domains`` remain
    readable.  Both at once are rejected, since neither should silently win.
    """

    list_key = _check_preset(payload, internal=False)
    imported = copy.deepcopy(payload)
    if list_key == "sections":
        imported["domains"] = imported.pop("sections")
    return imported
