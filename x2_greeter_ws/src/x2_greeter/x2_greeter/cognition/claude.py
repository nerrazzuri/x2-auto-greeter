"""The cloud backend: one Claude call answers three questions at once.

Is this really a person? What should the robot say? Which gesture fits?

One call per greeting event, never per frame — a per-frame cloud detector at
2 fps would cost roughly $50 per hour of idle standing and add seconds of
latency to every frame (spec section 8.1). The local detector decides *when*
to ask; this decides *what to do*.

Every failure path raises BackendUnavailable so GreetingPolicy can fall back
to a canned greeting. Nothing here ever raises a vendor exception into the
ROS node, and image bytes are never placed in a log or an exception message.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional, Sequence

import anthropic

from x2_greeter.cognition.port import BackendUnavailable
from x2_greeter.core.types import JpegFrame, SceneContext, Verdict

SYSTEM_PROMPT = (
    'You are the greeting module of an AgiBot X2 humanoid robot in a public space. '
    'You are shown one still frame from the robot\'s head camera at the moment a '
    'local detector believes somebody has stopped in front of it.\n\n'
    'Answer three questions:\n'
    '1. Is there really a person there? Say no for a mannequin, a poster, a '
    'reflection, a coat on a stand, or an empty corridor.\n'
    '2. What should the robot say? One short, warm, spoken sentence — at most 15 '
    'words. It will be read aloud by a text-to-speech engine, so write plain words '
    'with no emoji, markdown, stage directions or parentheses.\n'
    '3. Which gesture fits? Choose from the list you are given; nothing else.\n\n'
    'A person facing away still gets greeted — orientation is NOT a reason to say '
    'no. Report it in facing_robot so you can adapt: a spoken hello works for a '
    'back turned, but a bow or a handshake aimed at somebody\'s back does not.\n\n'
    'Do not describe the person\'s appearance, guess their identity, age, gender, '
    'ethnicity, or comment on their body. Keep the greeting generic and friendly.'
)

_REQUIRED_FIELDS = ('person_present', 'facing_robot', 'confidence', 'greeting',
                    'gesture', 'reason')


def build_schema(enabled_gestures: Sequence[str]) -> dict:
    """The structured-output schema, with the gesture enum built at runtime.

    The enum is the configured allowlist, so the model cannot name a gesture
    the robot is not permitted to perform. The result is still re-validated in
    _to_verdict: a schema is a request, not a guarantee we choose to trust.
    """
    return {
        'type': 'object',
        'properties': {
            'person_present': {'type': 'boolean',
                               'description': 'Is a real human being visible?'},
            'facing_robot': {'type': 'boolean',
                             'description': 'Are they facing the camera? Advisory only.'},
            'confidence': {'type': 'number',
                           'description': 'Your confidence in person_present, 0.0 to 1.0.'},
            'greeting': {'type': 'string',
                         'description': 'One short spoken sentence, at most 15 words.'},
            'gesture': {'type': 'string', 'enum': list(enabled_gestures),
                        'description': 'The gesture that best fits what you see and say.'},
            'reason': {'type': 'string',
                       'description': 'One clause explaining person_present.'},
        },
        'required': list(_REQUIRED_FIELDS),
        'additionalProperties': False,
    }


class ClaudeBackend:
    name = 'claude'

    def __init__(self, enabled_gestures: Sequence[str], model: str = 'claude-opus-5',
                 effort: str = 'low', timeout_s: float = 2.5, client=None,
                 logger: Optional[logging.Logger] = None) -> None:
        self._enabled = tuple(enabled_gestures)
        self._model = model
        self._effort = effort
        self._timeout_s = float(timeout_s)
        self._schema = build_schema(self._enabled)
        self._log = logger or logging.getLogger(__name__)
        # Constructing anthropic.Anthropic() reads ANTHROPIC_API_KEY and raises
        # if it is absent; the node checks for the key first and picks the
        # canned backend instead, so this only runs when a key exists.
        self._client = client if client is not None else anthropic.Anthropic()

    def confirm_and_compose(self, frame: Optional[JpegFrame],
                            ctx: SceneContext) -> Verdict:
        if frame is None:
            raise BackendUnavailable('no frame supplied to the cloud backend')

        try:
            response = self._client.with_options(
                timeout=self._timeout_s, max_retries=0,
            ).messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                thinking={'type': 'adaptive'},
                output_config={
                    'effort': self._effort,
                    'format': {'type': 'json_schema', 'schema': self._schema},
                },
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'image',
                         'source': {
                             'type': 'base64',
                             'media_type': frame.media_type,
                             'data': base64.standard_b64encode(frame.data).decode('ascii'),
                         }},
                        {'type': 'text', 'text': self._build_prompt(ctx)},
                    ],
                }],
            )
        # Most specific first. Every branch becomes BackendUnavailable, and no
        # branch is allowed to carry image bytes in its message.
        except anthropic.APITimeoutError as exc:
            raise BackendUnavailable(f'timed out after {self._timeout_s}s') from exc
        except anthropic.NotFoundError as exc:
            raise BackendUnavailable(f'model {self._model} not found') from exc
        except anthropic.RateLimitError as exc:
            raise BackendUnavailable('rate limited') from exc
        except anthropic.AuthenticationError as exc:
            raise BackendUnavailable('authentication rejected') from exc
        except anthropic.APIStatusError as exc:
            raise BackendUnavailable(f'API status {exc.status_code}') from exc
        except anthropic.APIConnectionError as exc:
            raise BackendUnavailable('API unreachable') from exc
        except Exception as exc:                       # noqa: BLE001 - never reach the node
            raise BackendUnavailable(f'unexpected backend failure: {type(exc).__name__}') from exc

        return self._to_verdict(self._extract_json(response))

    def _build_prompt(self, ctx: SceneContext) -> str:
        if ctx.center_offset < -0.05:
            where = 'slightly to your left'
        elif ctx.center_offset > 0.05:
            where = 'slightly to your right'
        else:
            where = 'directly ahead'
        gestures = ', '.join(self._enabled)
        return (
            f'The local detector reports a person about {ctx.distance_m:.1f} metres '
            f'away, {where}.\n'
            f'Gestures the robot may perform right now: {gestures}.\n'
            'Confirm the person, compose the greeting, and choose the gesture.'
        )

    @staticmethod
    def _extract_json(response) -> dict:
        """Pull the JSON out of the first text block.

        output_config.format guarantees the response contains a text block of
        valid JSON, but adaptive thinking may emit a thinking block first, so
        we search rather than index.
        """
        text = None
        for block in getattr(response, 'content', []) or []:
            if getattr(block, 'type', None) == 'text':
                text = block.text
                break
        if text is None:
            raise BackendUnavailable('response contained no text block')
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise BackendUnavailable('malformed JSON in response') from exc
        if not isinstance(payload, dict):
            raise BackendUnavailable('malformed JSON in response: not an object')
        return payload

    def _to_verdict(self, payload: dict) -> Verdict:
        missing = [field for field in _REQUIRED_FIELDS if field not in payload]
        if missing:
            raise BackendUnavailable(f'response missing field(s): {", ".join(missing)}')

        person_present = bool(payload['person_present'])
        greeting = str(payload['greeting']).strip()
        if person_present and not greeting:
            raise BackendUnavailable('empty greeting for a positive verdict')

        # The schema constrains the enum, but an LLM answer is never trusted to
        # index a motion ID. An unrecognised name becomes None and the selector
        # picks at random (spec section 9).
        gesture = payload.get('gesture')
        if gesture not in self._enabled:
            if gesture is not None:
                self._log.warning('backend proposed gesture %r outside the allowlist', gesture)
            gesture = None

        try:
            confidence = float(payload['confidence'])
        except (TypeError, ValueError):
            confidence = 0.0

        return Verdict(
            person_present=person_present,
            facing_robot=bool(payload['facing_robot']),
            confidence=confidence,
            greeting=greeting,
            gesture=gesture,
            reason=str(payload['reason'])[:200],
            source=self.name,
        )
