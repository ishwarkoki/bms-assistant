"""Provider port and registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from bms_assistant.domain.models import Availability, Job


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    label: str
    description: str
    requires_booking_url: bool = False
    dev_only: bool = False
    # True only for providers that actually observe BMS ticket availability.
    # Reminder and mock providers fire on a clock, so their alerts must not be
    # presented as proof that tickets are on sale.
    verifies_availability: bool = False


class TicketProvider(Protocol):
    """A source of ticket availability.

    Implementations must be side-effect free: a check only observes availability.
    They must never attempt a purchase, payment, CAPTCHA solve, or auth bypass.
    """

    @property
    def info(self) -> ProviderInfo: ...

    async def check(self, job: Job) -> Availability: ...


class ProviderRegistry:
    def __init__(self, providers: Iterable[TicketProvider]) -> None:
        self._providers = {provider.info.name: provider for provider in providers}

    def get(self, name: str) -> TicketProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown provider: {name}") from exc

    def list(self) -> list[ProviderInfo]:
        return [provider.info for provider in self._providers.values()]

    def __contains__(self, name: object) -> bool:
        return name in self._providers
