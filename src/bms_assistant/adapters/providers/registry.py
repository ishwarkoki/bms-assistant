"""Built-in provider registry."""

from __future__ import annotations

from bms_assistant.adapters.providers.mock import MockProvider
from bms_assistant.adapters.providers.reminder import ReminderProvider
from bms_assistant.ports.providers import ProviderRegistry


def build_default_registry() -> ProviderRegistry:
    return ProviderRegistry([MockProvider(), ReminderProvider()])
