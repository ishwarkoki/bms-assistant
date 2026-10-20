# Provider development

A provider is a small adapter around an availability source. The domain only
needs a yes/no answer and, if available, a handoff URL.

## Contract

```python
from bms_assistant.domain.models import Availability, Job
from bms_assistant.ports.providers import ProviderInfo


class MyProvider:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="my-provider",
            label="My provider",
            description="What this provider checks.",
            requires_booking_url=False,
            dev_only=False,
        )

    async def check(self, job: Job) -> Availability: ...
```

Rules:

1. **Observe only.** No purchases, payments, seat locks, OTP flows, CAPTCHA
   solving, or access-control bypasses.
2. **Be fast and cancellable.** The job manager can cancel a check when a watch
   is paused or deleted.
3. **Raise on transient failure.** The manager retries with backoff and fails
   the watch after `BMS_MAX_CONSECUTIVE_ERRORS`.
4. **Return a URL on success.** The UI and Telegram notifier use it for the
   handoff button.
5. **Do not store credentials in a job.** Keep secrets in the environment and
   inject them into the adapter.

## Register it

Add the provider to `build_default_registry()`:

```python
def build_default_registry() -> ProviderRegistry:
    return ProviderRegistry([MockProvider(), ReminderProvider(), MyProvider()])
```

The web form and Telegram wizard read provider metadata automatically.

## Testing

Use `MockProvider` for deterministic UI and notification tests. For a real
provider, write unit tests around the response parser and use recorded fixtures
rather than hitting a live service in CI.
