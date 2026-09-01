"""The seam between the greeter and whoever decides what to say.

Adding Grok, GPT or Gemini is one new file implementing this Protocol plus one
new value for the backend.provider parameter. Each adapter uses its own
vendor's official SDK — no lowest-common-denominator shim, so the Claude path
stays idiomatic (spec section 8.2).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from x2_greeter.core.types import JpegFrame, SceneContext, Verdict


class BackendUnavailable(Exception):
    """The backend could not answer: network, auth, rate limit, bad response.

    Backends raise this instead of leaking vendor exception types, so
    GreetingPolicy can fall back without importing every vendor's SDK.
    """


@runtime_checkable
class GreetingBackend(Protocol):
    name: str

    def confirm_and_compose(self, frame: Optional[JpegFrame],
                            ctx: SceneContext) -> Verdict:
        """Confirm the person and compose a greeting, or raise BackendUnavailable.

        `frame` is None for backends that do not look at the image; a cloud
        backend must raise BackendUnavailable rather than guess if it is None.
        """
        ...
