"""JSON preset import and export utilities for numerical fin configurations.

In the user-facing JSON preset files downloaded by students, the axial segments
of the fin are named ``sections``. Internally, the numerical solver (following
the original Excel/VBA nomenclature) refers to the same list of segments as
``domains``. This module converts cleanly between the two representations at
the application boundary without altering numerical values or physical units.
"""

from __future__ import annotations  # Postpone evaluation of type annotations (PEP 563)

import copy  # Used to create deep copies so caller dictionaries are never mutated in place
from typing import Any  # Generic type annotation for JSON-like dictionary structures


# Tuple of the four required top-level dictionary keys besides `sections`/`domains`:
# - `controls`: time-stepping method, duration, time step, and printing interval
# - `geometry`: cross-sectional shape and millimetre dimensions
# - `temperature`: boundary conditions, radiation toggles, and initial temperatures
# - `graphs`: selected cell numbers and time percentages for 2D plotting
_OTHER_FIELDS = ("controls", "geometry", "temperature", "graphs")  # Required top-level sections


def _check_preset(config: Any, *, internal: bool) -> str:
    """Validate the structure of a configuration dictionary and return its segment-list key."""
    # Ensure the top-level JSON payload is a dictionary (JSON object) rather than a list or scalar.
    if not isinstance(config, dict):
        # Raise a descriptive error if the root JSON value is not an object.
        raise ValueError("Numerical inputs JSON must be an object.")
    # Check whether the public key `sections` is present in the dictionary.
    has_sections = "sections" in config
    # Check whether the internal/legacy key `domains` is present in the dictionary.
    has_domains = "domains" in config
    # Reject payloads that contain both keys simultaneously to avoid ambiguity over which list wins.
    if has_sections and has_domains:
        # Inform the user that only one of `sections` or `domains` may be supplied.
        raise ValueError("Numerical inputs JSON cannot contain both sections and domains.")
    # Reject payloads that omit both `sections` and `domains`.
    if not has_sections and not has_domains:
        # Explain that a list of fin sections is required.
        raise ValueError("Numerical inputs JSON needs a sections list (legacy domains is also accepted).")
    # When exporting from internal solver state (`internal=True`), require the internal `domains` key.
    if internal and not has_domains:
        # Internal configurations must always store axial segments under `domains`.
        raise ValueError("Internal numerical inputs must contain domains.")

    # Loop over the four remaining top-level groups (`controls`, `geometry`, `temperature`, `graphs`).
    for key in _OTHER_FIELDS:
        # Verify that each top-level group exists and is itself a dictionary.
        if not isinstance(config.get(key), dict):
            # Report which top-level configuration group is missing or malformed.
            raise ValueError(f"Numerical inputs JSON needs a {key} object.")
    # Determine which key name (`domains` or `sections`) holds the list of axial fin segments.
    list_key = "domains" if has_domains else "sections"
    # Retrieve the list of segment dictionaries from the configuration.
    entries = config[list_key]
    # A valid fin model must have at least one axial segment (`len(entries) >= 1`).
    if not isinstance(entries, list) or not entries:
        # Raise an error if the segment list is missing, empty, or not a list.
        raise ValueError(f"Numerical inputs JSON needs a nonempty {list_key} list.")
    # Verify that every element inside the segment list is a dictionary of segment properties.
    if any(not isinstance(entry, dict) for entry in entries):
        # Raise an error if any segment entry is not a dictionary object.
        raise ValueError(f"Every {list_key} entry must be an object.")
    # Return the validated segment-list key (`"domains"` or `"sections"`).
    return list_key


def export_numerical_preset(config: dict[str, Any]) -> dict[str, Any]:
    """Convert an internal solver configuration (`domains`) into a JSON-exportable preset (`sections`).

    Any `None` values representing unfilled UI fields are preserved as JSON `null`
    so a partially filled form can also be exported and reloaded if desired.
    """
    # Validate that `config` is a well-formed internal configuration containing `domains`.
    _check_preset(config, internal=True)
    # Deep-copy the configuration so the live session state is not modified during export.
    exported = copy.deepcopy(config)
    # Rename the top-level `domains` key to the user-friendly `sections` key.
    exported["sections"] = exported.pop("domains")
    # Return the exported dictionary ready for `json.dumps(...)`.
    return exported


def import_numerical_preset(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert a loaded JSON preset (`sections` or legacy `domains`) into an internal solver configuration.

    Both modern presets (`sections`) and legacy presets (`domains`) are accepted,
    and the returned dictionary always uses `domains` as expected by `simulate()`.
    """
    # Validate the JSON structure and identify whether segments are stored under `sections` or `domains`.
    list_key = _check_preset(payload, internal=False)
    # Deep-copy the incoming dictionary to avoid mutating caller data.
    imported = copy.deepcopy(payload)
    # If the file uses the public `sections` key, rename it to `domains` for internal solver use.
    if list_key == "sections":
        # Pop `sections` and assign it to `domains`.
        imported["domains"] = imported.pop("sections")
    # Return the normalized internal configuration dictionary.
    return imported
