"""Evidence-backed claim generation and validation for public-facing copy."""

from __future__ import annotations

from raid_editor.growth.models import ClaimRecord, CoverageRecord


def validate_coverage_claims(
    coverage: CoverageRecord,
    *,
    content_id: str,
) -> list[ClaimRecord]:
    """Build conservative claims from the coverage actually present in an asset.

    Overall raid results are preserved separately from recorded and edited footage.
    They never fill a footage gap or turn a partial recording into a recorded full clear.
    """

    edited_count = len(coverage.edited_bosses)
    recorded_count = len(coverage.recorded_bosses)
    evidence = list(dict.fromkeys(coverage.evidence_ids))
    claims: list[ClaimRecord] = []

    claims.append(
        ClaimRecord(
            claim_id=f"{content_id}:progress",
            kind="progress",
            text=f"{edited_count}/{coverage.expected_bosses}",
            state="allowed",
            reason="The progress claim counts unique bosses present in the edited asset.",
            evidence_ids=evidence,
        )
    )
    claims.append(
        ClaimRecord(
            claim_id=f"{content_id}:overall-result",
            kind="overall_result",
            text=(
                f"Overall raid result: {coverage.overall_bosses_killed}/{coverage.expected_bosses}"
            ),
            state="allowed",
            reason="The overall result is stored separately and never fills a footage gap.",
            evidence_ids=evidence,
        )
    )
    claims.append(
        ClaimRecord(
            claim_id=f"{content_id}:recorded-range",
            kind="recorded_range",
            text=f"Recorded footage: {recorded_count}/{coverage.expected_bosses} bosses",
            state="allowed",
            reason="The recorded range counts unique winning encounters present in source.",
            evidence_ids=evidence,
        )
    )

    if coverage.confirmed_raid_size is None:
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:raid-size",
                kind="raid_size",
                text="unknown raid size",
                state="manual_review",
                reason="Raid size is not confirmed by configuration or encounter evidence.",
                evidence_ids=evidence,
            )
        )
    else:
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:raid-size",
                kind="raid_size",
                text=f"{coverage.confirmed_raid_size}M",
                state="allowed",
                reason="Raid size is confirmed for the edited winning pulls.",
                evidence_ids=evidence,
            )
        )

    heroic_names = {name.casefold() for name in coverage.heroic_bosses}
    unknown_names = {name.casefold() for name in coverage.unknown_difficulty_bosses}
    for index, boss in enumerate(coverage.edited_bosses, start=1):
        folded = boss.casefold()
        if folded in unknown_names:
            difficulty = "Unknown"
            state = "manual_review"
            reason = "This edited encounter lacks confirmed per-boss difficulty evidence."
        elif folded in heroic_names:
            difficulty = "Heroic"
            state = "allowed"
            reason = "The edited encounter has confirmed Heroic evidence."
        else:
            difficulty = "Normal"
            state = "allowed"
            reason = "The edited encounter has confirmed non-Heroic evidence."
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:difficulty:{index:02d}",
                kind="difficulty",
                text=f"{boss}: {difficulty}",
                state=state,  # type: ignore[arg-type]
                reason=reason,
                evidence_ids=evidence,
            )
        )

    heroic_count = len(coverage.heroic_bosses)
    if coverage.unknown_difficulty_bosses:
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:heroic-count",
                kind="heroic_count",
                text=f"at least {heroic_count}HC",
                state="manual_review",
                reason=(
                    "Some edited boss difficulties are unresolved, so an exact Heroic count "
                    "cannot be generated automatically."
                ),
                evidence_ids=evidence,
            )
        )
    else:
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:heroic-count",
                kind="heroic_count",
                text=f"{heroic_count}HC",
                state="allowed",
                reason="Every edited boss has confirmed difficulty evidence.",
                evidence_ids=evidence,
            )
        )

    footage_is_full = (
        edited_count == coverage.expected_bosses
        and recorded_count == coverage.expected_bosses
        and coverage.overall_bosses_killed == coverage.expected_bosses
        and coverage.ending_evidence_complete
        and len(coverage.winning_ending_bosses) == coverage.expected_bosses
        and {name.casefold() for name in coverage.edited_bosses}
        == {name.casefold() for name in coverage.winning_ending_bosses}
    )
    if footage_is_full:
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:full-clear",
                kind="full_clear",
                text="Full Clear",
                state="allowed",
                reason=(
                    "All expected bosses are recorded and edited, every ending is evidenced, "
                    "and the overall result is complete."
                ),
                evidence_ids=evidence,
            )
        )
    else:
        gaps: list[str] = []
        if recorded_count != coverage.expected_bosses:
            gaps.append(f"recorded footage covers {recorded_count}/{coverage.expected_bosses}")
        if edited_count != coverage.expected_bosses:
            gaps.append(f"edited asset covers {edited_count}/{coverage.expected_bosses}")
        if coverage.overall_bosses_killed != coverage.expected_bosses:
            gaps.append(
                f"overall result is {coverage.overall_bosses_killed}/{coverage.expected_bosses}"
            )
        if not coverage.ending_evidence_complete:
            gaps.append("complete winning endings are not evidenced")
        missing_endings = {name.casefold(): name for name in coverage.edited_bosses}.keys() - {
            name.casefold() for name in coverage.winning_ending_bosses
        }
        if missing_endings:
            gaps.append(
                f"winning endings are missing for {len(missing_endings)} edited encounter(s)"
            )
        claims.append(
            ClaimRecord(
                claim_id=f"{content_id}:full-clear",
                kind="full_clear",
                text="Full Clear",
                state="rejected",
                reason="Cannot claim a recorded full clear: " + "; ".join(gaps) + ".",
                evidence_ids=evidence,
            )
        )
    return claims


def allowed_copy_tokens(claims: list[ClaimRecord]) -> list[str]:
    """Return only reviewed, allowed claim text for title and description builders."""

    return [claim.text for claim in claims if claim.state == "allowed"]


def require_claim_allowed(claims: list[ClaimRecord], kind: str) -> ClaimRecord:
    """Return one allowed claim or raise a public-copy validation error."""

    matches = [claim for claim in claims if claim.kind == kind and claim.state == "allowed"]
    if len(matches) != 1:
        raise ValueError(f"Public copy requires exactly one allowed {kind} claim")
    return matches[0]
