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

#: Spoken by a TTS engine, so: plain words, no parentheses, no emoji, and the
#: wake word kept intact and unpunctuated in the middle of the sentence.
DEFAULT_INVITATIONS = (
    'Say Hi Lumi and I will be happy to chat.',
    'Call out Hi Lumi and we can have a proper conversation.',
    'Just say Hi Lumi whenever you would like to talk.',
    'If you fancy a chat, say Hi Lumi and I am all ears.',
    'Say Hi Lumi and we can talk about whatever you like.',
)


def append_invitation(greeting: str, invitations: Sequence[str],
                      rng: Optional[random.Random] = None) -> str:
    """Return the greeting with one invitation appended, or unchanged.

    An empty `invitations` list turns the feature off -- a robot whose
    interaction system is reachable some other way should not be telling
    people to say a wake word.

    A blank greeting stays blank: SpeechDispatcher refuses to speak an empty
    greeting, and turning "nothing to say" into "say Hi Lumi" would make the
    robot address people the cloud has just decided are not there.
    """
    greeting = (greeting or '').strip()
    choices = [str(i).strip() for i in invitations if str(i).strip()]
    if not greeting or not choices:
        return greeting
    chooser = rng if rng is not None else random
    return f'{greeting} {chooser.choice(choices)}'
