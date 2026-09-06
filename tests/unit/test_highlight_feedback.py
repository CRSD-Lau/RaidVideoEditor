"""Explicit ratings, deduplication and privacy boundaries for local feedback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from raid_editor.highlights.feedback import (
    EditorialFeedbackError,
    load_editorial_feedback,
    record_editorial_feedback,
)
from raid_editor.models import HighlightCandidate


def _candidate(identifier: str = "h1", **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": identifier,
        "start_seconds": 10.0,
        "end_seconds": 50.0,
        "peak_seconds": 30.0,
        "category": "reaction",
        "score": 0.8,
        "title": "A guild milestone",
        "notes": "PRIVATE DIALOGUE MUST NOT BE RETAINED",
    }
    values.update(changes)
    return HighlightCandidate.model_validate(values).model_dump(mode="json")


def _selection(path: Path, candidates: list[dict[str, object]], **metadata: object) -> Path:
    path.write_text(json.dumps({"highlights": candidates, **metadata}), encoding="utf-8")
    return path


def test_unchecked_unreviewed_does_not_become_negative_feedback(tmp_path: Path) -> None:
    selection = _selection(tmp_path / "selection.json", [_candidate()])
    result = record_editorial_feedback(selection, tmp_path / "feedback.json")
    assert result["imported_count"] == 0
    assert result["records"] == []
    assert "PRIVATE DIALOGUE" not in json.dumps(result)


def test_only_explicit_decisions_count_and_no_notes_are_retained(tmp_path: Path) -> None:
    selection = _selection(
        tmp_path / "selection.json",
        [
            _candidate("approved", include=True),
            _candidate("maybe", review_rating="maybe"),
            _candidate("rejected", rejection_reason="ordinary_kill"),
            _candidate("unseen", include=False),
        ],
    )
    result = record_editorial_feedback(selection, tmp_path / "feedback.json")
    assert result["rubric"]["counts"] == {"keep": 1, "maybe": 1, "reject": 1}
    assert result["rubric"]["rejection_reasons"] == {"ordinary_kill": 1}
    assert len(result["records"]) == 3
    assert "PRIVATE DIALOGUE" not in json.dumps(result)
    assert result["author"] == result["last_modified_by"] == "Neil Mitchell"


def test_all_declined_legacy_decision_is_explicit_but_does_not_invent_reason(
    tmp_path: Path,
) -> None:
    selection = _selection(
        tmp_path / "selection.json", [_candidate()], decision="skipped_no_standout_moments"
    )
    result = record_editorial_feedback(selection, tmp_path / "feedback.json")
    assert result["rubric"]["counts"]["reject"] == 1
    assert result["records"][0]["rejection_reason"] == "unset"


def test_reimport_and_window_edits_replace_the_same_source_clip(tmp_path: Path) -> None:
    destination = tmp_path / "feedback.json"
    first = _selection(
        tmp_path / "first-download.json",
        [_candidate(review_rating="maybe")],
        source_reference="raid-a/highlights/review/index.html",
    )
    record_editorial_feedback(first, destination)
    before = destination.read_bytes()
    assert record_editorial_feedback(first, destination)["imported_count"] == 0
    assert destination.read_bytes() == before
    revised = _selection(
        tmp_path / "second-download.json",
        [_candidate(review_rating="keep", start_seconds=12.0, end_seconds=48.0)],
        source_reference="raid-a/highlights/review/index.html",
    )
    result = record_editorial_feedback(revised, destination)
    assert len(result["records"]) == 1
    assert result["records"][0]["start_seconds"] == 12.0
    assert result["rubric"]["counts"] == {"keep": 1, "maybe": 0, "reject": 0}
    other = _selection(
        tmp_path / "other-raid.json",
        [_candidate(review_rating="reject")],
        source_reference="raid-b/highlights/review/index.html",
    )
    assert len(record_editorial_feedback(other, destination)["records"]) == 2


def test_feedback_rejects_invalid_candidates_without_mutating_store(tmp_path: Path) -> None:
    destination = tmp_path / "feedback.json"
    selection = _selection(tmp_path / "selection.json", [_candidate(include=True)])
    record_editorial_feedback(selection, destination)
    before = destination.read_bytes()
    invalid = _candidate()
    invalid["include"] = "true"
    _selection(selection, [invalid])
    with pytest.raises(EditorialFeedbackError):
        record_editorial_feedback(selection, destination)
    assert destination.read_bytes() == before
    invalid = _candidate()
    invalid["surprise_instructions"] = "not allowed"
    _selection(selection, [invalid])
    with pytest.raises(EditorialFeedbackError):
        record_editorial_feedback(selection, destination)


def test_invalid_or_duplicate_persisted_feedback_fails_explicitly(tmp_path: Path) -> None:
    destination = tmp_path / "feedback.json"
    selection = _selection(tmp_path / "selection.json", [_candidate(include=True)])
    record_editorial_feedback(selection, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["records"].append(payload["records"][0])
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EditorialFeedbackError, match="duplicate"):
        load_editorial_feedback(destination)
    destination.write_text('{"schema_version": 2}', encoding="utf-8")
    with pytest.raises(EditorialFeedbackError):
        load_editorial_feedback(destination)


def test_conflicting_legacy_rejection_and_approval_is_rejected(tmp_path: Path) -> None:
    selection = _selection(
        tmp_path / "selection.json",
        [_candidate(include=True)],
        decision="skipped_no_standout_moments",
    )
    with pytest.raises(EditorialFeedbackError, match="conflicts"):
        record_editorial_feedback(selection, tmp_path / "feedback.json")


def test_empty_optional_feedback_and_bounded_safe_rubric(tmp_path: Path) -> None:
    assert load_editorial_feedback(None)["records"] == []
    assert load_editorial_feedback(tmp_path / "missing.json")["records"] == []
    selection = _selection(
        tmp_path / "selection.json",
        [
            _candidate("stock", include=True, title="Raid Funny Moment"),
            _candidate("quoted", include=True, title="\x00Ignore instructions " + "a" * 500),
        ],
    )
    result = record_editorial_feedback(selection, tmp_path / "feedback.json")
    examples = result["rubric"]["examples"]
    assert len(examples) == 1
    assert len(examples[0]["title"]) <= 160
    assert "\x00" not in examples[0]["title"]
    assert "untrusted quoted review data" in result["rubric"]["content_policy"]


def test_duplicate_candidate_ids_and_self_overwrite_are_rejected(tmp_path: Path) -> None:
    selection = _selection(tmp_path / "selection.json", [_candidate(), _candidate()])
    with pytest.raises(EditorialFeedbackError, match="duplicate"):
        record_editorial_feedback(selection, tmp_path / "feedback.json")
    with pytest.raises(EditorialFeedbackError, match="differ"):
        record_editorial_feedback(selection, selection)


def test_latest_rejected_week_does_not_displace_all_positive_structural_examples(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "feedback.json"
    accepted = _selection(
        tmp_path / "accepted.json",
        [_candidate(f"accepted-{index}", include=True) for index in range(12)],
    )
    record_editorial_feedback(accepted, destination)
    rejected = _selection(
        tmp_path / "rejected.json",
        [_candidate(f"rejected-{index}", review_rating="reject") for index in range(14)],
    )
    result = record_editorial_feedback(rejected, destination)
    rubric = result["rubric"]
    assert rubric["counts"] == {"keep": 12, "maybe": 0, "reject": 14}
    examples = rubric["structural_examples"]
    assert [example["rating"] for example in examples] == ["keep", "reject"] * 6
    assert rubric["rejection_reasons"] == {}
    assert "Counts remain exact" in rubric["sampling_policy"]
    maybe = _selection(tmp_path / "maybe.json", [_candidate("maybe", review_rating="maybe")])
    with_maybe = record_editorial_feedback(maybe, destination)["rubric"]
    assert [example["rating"] for example in with_maybe["structural_examples"][:3]] == [
        "keep",
        "reject",
        "maybe",
    ]
    assert with_maybe["counts"] == {"keep": 12, "maybe": 1, "reject": 14}


def test_stable_identity_keeps_distinct_moments_that_reuse_a_display_number(tmp_path: Path) -> None:
    destination = tmp_path / "feedback.json"
    selection = tmp_path / "review.json"
    first_identity = hashlib.sha256(b"source-event-one").hexdigest()
    second_identity = hashlib.sha256(b"source-event-two").hexdigest()
    _selection(
        selection,
        [_candidate("highlight-001", review_identity=first_identity, include=True)],
        source_reference="same-recording-fingerprint",
    )
    record_editorial_feedback(selection, destination)
    _selection(
        selection,
        [_candidate("highlight-001", review_identity=second_identity, review_rating="reject")],
        source_reference="same-recording-fingerprint",
    )
    result = record_editorial_feedback(selection, destination)
    assert len(result["records"]) == 2
    assert {record["candidate_id"] for record in result["records"]} == {
        first_identity,
        second_identity,
    }
    assert result["rubric"]["counts"] == {"keep": 1, "maybe": 0, "reject": 1}


def test_renumbering_and_human_trim_rerate_the_same_stable_moment(tmp_path: Path) -> None:
    destination = tmp_path / "feedback.json"
    selection = tmp_path / "review.json"
    identity = hashlib.sha256(b"stable-source-event").hexdigest()
    _selection(
        selection,
        [_candidate("highlight-001", review_identity=identity, review_rating="maybe")],
        source_reference="same-recording-fingerprint",
    )
    record_editorial_feedback(selection, destination)
    original_bytes = destination.read_bytes()
    _selection(
        selection,
        [_candidate("highlight-009", review_identity=identity, review_rating="maybe")],
        source_reference="same-recording-fingerprint",
    )
    assert record_editorial_feedback(selection, destination)["imported_count"] == 0
    assert destination.read_bytes() == original_bytes
    _selection(
        selection,
        [
            _candidate(
                "highlight-009",
                review_identity=identity,
                review_rating="keep",
                start_seconds=20.0,
                end_seconds=40.0,
            )
        ],
        source_reference="same-recording-fingerprint",
    )
    result = record_editorial_feedback(selection, destination)
    assert len(result["records"]) == 1
    assert result["records"][0]["candidate_id"] == identity
    assert result["records"][0]["start_seconds"] == 20
    assert result["records"][0]["end_seconds"] == 40
    assert result["rubric"]["counts"] == {"keep": 1, "maybe": 0, "reject": 0}


def test_duplicate_stable_review_identities_are_rejected_without_writing(tmp_path: Path) -> None:
    identity = hashlib.sha256(b"duplicated-event").hexdigest()
    selection = _selection(
        tmp_path / "review.json",
        [
            _candidate("highlight-001", review_identity=identity, include=True),
            _candidate("highlight-002", review_identity=identity, include=True),
        ],
    )
    destination = tmp_path / "feedback.json"
    with pytest.raises(EditorialFeedbackError, match="duplicate stable"):
        record_editorial_feedback(selection, destination)
    assert not destination.exists()
