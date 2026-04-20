"""
ai/experiments.py
TASK-170: Experiment / feature flags system for Artifex360.

Provides a typed enum of experiment IDs and a singleton ``ExperimentFlags``
manager that reads/writes overrides via ``config/settings.py``.  All flags
default to ``False`` (disabled) until explicitly enabled.

TASK-203: ``_defaults`` is now a ``types.MappingProxyType`` (immutable view)
so that accidental mutation of the class-level dict fails loudly with a
``TypeError``.

Usage::

    from ai.experiments import experiment_flags, ExperimentId

    if experiment_flags.is_enabled(ExperimentId.AUTO_APPROVAL):
        ...
"""

import enum
import logging
import types
from typing import Any

logger = logging.getLogger(__name__)


class ExperimentId(str, enum.Enum):
    """Identifiers for experiment / feature flags."""

    AUTO_APPROVAL = "auto_approval"
    FOLDED_CONTEXT = "folded_context"
    FILE_TRACKING = "file_tracking"
    CUSTOM_MODES = "custom_modes"
    CUSTOM_TOOLS = "custom_tools"
    NON_DESTRUCTIVE_TRUNCATION = "non_destructive_truncation"


class ExperimentFlags:
    """Manage experiment flags backed by ``config/settings.py``.

    Every flag defaults to ``False``.  Overrides are stored in the
    ``experiments`` dict inside the settings JSON file so they survive
    restarts.

    This class intentionally uses **lazy imports** for ``settings`` to
    avoid circular-import or import-time I/O issues (matching the
    project convention established in TASK-029 / TASK-122).
    """

    # Default state for every known flag -- all disabled.
    # TASK-203: Wrapped in MappingProxyType to prevent accidental mutation.
    _defaults: types.MappingProxyType = types.MappingProxyType(
        {flag.value: False for flag in ExperimentId}
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _settings():
        """Lazy import of the module-level settings singleton."""
        from config.settings import settings  # noqa: WPS433
        return settings

    @staticmethod
    def _validate(flag_id: Any) -> ExperimentId:
        """Coerce *flag_id* to an ``ExperimentId``, raising on invalid."""
        if isinstance(flag_id, ExperimentId):
            return flag_id
        try:
            return ExperimentId(flag_id)
        except ValueError:
            raise ValueError(
                f"Unknown experiment flag: {flag_id!r}. "
                f"Valid flags: {[f.value for f in ExperimentId]}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self, flag_id: ExperimentId) -> bool:
        """Return whether *flag_id* is currently enabled."""
        flag = self._validate(flag_id)
        overrides: dict = self._settings().get("experiments", {})
        return bool(overrides.get(flag.value, self._defaults[flag.value]))

    def set_enabled(self, flag_id: ExperimentId, enabled: bool) -> None:
        """Enable or disable *flag_id* and persist to settings."""
        flag = self._validate(flag_id)
        s = self._settings()
        overrides: dict = dict(s.get("experiments", {}))
        overrides[flag.value] = bool(enabled)
        s.set("experiments", overrides, _internal=True)
        s.save()
        logger.info("Experiment flag %s set to %s", flag.value, enabled)

    def get_all(self) -> dict[str, bool]:
        """Return a dict of **all** flags with their effective values.

        Suitable for UI consumption -- always contains every known flag.
        """
        overrides: dict = self._settings().get("experiments", {})
        return {
            flag_id: bool(overrides.get(flag_id, default))
            for flag_id, default in self._defaults.items()
        }


# Module-level singleton (mirrors ``settings`` pattern).
experiment_flags = ExperimentFlags()
