"""Inviting the person to start a conversation.

The X2's interaction system only opens a voice session on its wake word. The
greeter cannot open one for it -- `InteractWithId(BEHAVIOR_LISTENING)` returns
success and switches the behaviour group without starting a session, and
`AudioInput`, whose shape fits (audio in, session id out), is defined in the
message set but not served on the robot. So the robot greets somebody, they
answer, and nothing happens.

Saying the wake word out loud is the way through: the greeting ends by telling
the person the words that will actually work. It costs a sentence and needs no
interface the SDK does not have.

Appended after the greeting whatever produced it, canned or cloud, because the
cloud is free to write its own opening line and should not have to remember
this. Pure text, no ROS, so it is testable in the fast suite.
"""
from __future__ import annotations

import random
from typing import Optional, Sequence

#: What opens a voice session on the X2. It was "Hi Lumi" until 2026-09-17;
#: the robots answer to 灵犀灵犀, said twice. Written out here once, and the
#: invitations below, config/greeter.yaml and the tests all take it from
#: here, so changing the wake word again is one line rather than a search.
#:
#: Spelled for an English TTS voice to read aloud. Whether it is *pronounced*
#: so that a visitor repeating it wakes the robot can only be checked by
#: listening to the robot, not by any test in this repository.
WAKE_WORD = 'Lingxi Lingxi'

#: Spoken by a TTS engine, so: plain words, no parentheses, no emoji, and the
#: wake word kept intact and unpunctuated in the middle of the sentence.
DEFAULT_INVITATIONS = (
    f'Say {WAKE_WORD} and I will be happy to chat.',
    f'Call out {WAKE_WORD} and we can have a proper conversation.',
    f'Just say {WAKE_WORD} whenever you would like to talk.',
    f'If you fancy a chat, say {WAKE_WORD} and I am all ears.',
    f'Say {WAKE_WORD} and we can talk about whatever you like.',
)


def append_invitation(greeting: str, invitations: Sequence[str],
                      rng: Optional[random.Random] = None) -> str:
    """Return the greeting with one invitation appended, or unchanged.

    An empty `invitations` list turns the feature off -- a robot whose
    interaction system is reachable some other way should not be telling
    people to say a wake word.

    A blank greeting stays blank: SpeechDispatcher refuses to speak an empty
    greeting, and turning "nothing to say" into an invitation would make the
    robot address people the cloud has just decided are not there.
    """
    greeting = (greeting or '').strip()
    choices = [str(i).strip() for i in invitations if str(i).strip()]
    if not greeting or not choices:
        return greeting
    chooser = rng if rng is not None else random
    return f'{greeting} {chooser.choice(choices)}'
