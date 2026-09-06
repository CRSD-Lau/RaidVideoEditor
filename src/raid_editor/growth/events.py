"""Event markers that align edit decisions with later retention analysis."""

from __future__ import annotations

from pathlib import Path

from raid_editor.growth.models import TimelineEvent
from raid_editor.models import TimelineDocument
from raid_editor.util.paths import atomic_write_json


def build_timeline_events(
    timeline: TimelineDocument,
    *,
    intro_seconds: float = 0,
    boss_card_seconds: float = 0,
    outro_seconds: float = 0,
) -> list[TimelineEvent]:
    """Map presentation and boss-action boundaries onto final-video time."""

    events: list[TimelineEvent] = [
        TimelineEvent(event_id="intro-start", kind="intro_start", seconds=0, label="Intro")
    ]
    if intro_seconds > 0:
        events.append(
            TimelineEvent(
                event_id="intro-end",
                kind="intro_end",
                seconds=intro_seconds,
                label="Raid coverage begins",
            )
        )
    for index, clip in enumerate(timeline.clips, start=1):
        card_at = intro_seconds + clip.timeline_in
        action_at = card_at + boss_card_seconds
        source_ids = list(clip.pull_ids)
        events.append(
            TimelineEvent(
                event_id=f"boss-{index:02d}-card",
                kind="boss_card",
                seconds=card_at,
                label=clip.label,
                source_clip_ids=source_ids,
                metadata={"difficulty": clip.difficulty, "result": clip.result},
            )
        )
        events.append(
            TimelineEvent(
                event_id=f"boss-{index:02d}-action",
                kind="boss_action_start",
                seconds=action_at,
                label=f"{clip.label} action",
                source_clip_ids=source_ids,
            )
        )
        if index == 1:
            events.append(
                TimelineEvent(
                    event_id="first-payoff",
                    kind="first_payoff",
                    seconds=action_at,
                    label=f"First payoff: {clip.label}",
                    source_clip_ids=source_ids,
                )
            )
        if index > 1:
            events.append(
                TimelineEvent(
                    event_id=f"transition-{index:02d}",
                    kind="transition",
                    seconds=card_at,
                    label=f"Transition to {clip.label}",
                    source_clip_ids=source_ids,
                )
            )
    body_duration = timeline.duration_seconds
    outro_start = intro_seconds + body_duration
    if outro_seconds > 0:
        events.extend(
            [
                TimelineEvent(
                    event_id="cta",
                    kind="cta",
                    seconds=outro_start,
                    label="Subscribe and next video",
                ),
                TimelineEvent(
                    event_id="outro-start",
                    kind="outro_start",
                    seconds=outro_start,
                    label="Outro",
                ),
                TimelineEvent(
                    event_id="outro-end",
                    kind="outro_end",
                    seconds=outro_start + outro_seconds,
                    label="Video ends",
                ),
            ]
        )
    return sorted(events, key=lambda event: (event.seconds, event.event_id))


def write_timeline_events(events: list[TimelineEvent], destination: Path) -> None:
    atomic_write_json(destination, [event.model_dump(mode="json") for event in events])
