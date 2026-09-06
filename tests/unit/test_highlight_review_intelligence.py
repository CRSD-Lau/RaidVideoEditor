"""Review evidence presentation and the actual exported decision behavior."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from raid_editor.highlights.feedback import record_editorial_feedback
from raid_editor.highlights.review import (
    generate_highlight_comparison_page,
    generate_highlight_review_page,
)
from raid_editor.models import HighlightCandidate


def _page(tmp_path: Path, **changes: object) -> str:
    values: dict[str, object] = {
        "id": "h1",
        "title": "A save <script>bad()</script>",
        "start_seconds": 10.0,
        "end_seconds": 50.0,
        "peak_seconds": 30.0,
        "category": "clutch",
        "score": 0.9,
        "origin": "semantic",
        "rationale": "The tank survives <danger> and the group responds.",
        "setup_seconds": 12.0,
        "payoff_seconds": 37.0,
        "confidence": 0.8,
    }
    values.update(changes)
    candidate = HighlightCandidate.model_validate(values)
    destination = tmp_path / "index.html"
    generate_highlight_review_page(
        [candidate],
        {candidate.id: tmp_path / "assets" / "h1.webm"},
        destination,
        includes_game=True,
        includes_discord=True,
        includes_microphone=True,
        intelligence_status={"status": "degraded", "diagnostics": ["visual model unavailable"]},
    )
    return destination.read_text(encoding="utf-8")


def test_review_exposes_evidence_and_degraded_coverage_without_false_certainty(
    tmp_path: Path,
) -> None:
    document = _page(tmp_path)
    assert "Editorial intelligence: degraded; visual model unavailable" in document
    assert "Why review this:" in document
    assert "The tank survives &lt;danger&gt;" in document
    assert "Setup: 12.000s · Payoff: 37.000s" in document
    assert "model self-assessment; uncalibrated" in document
    assert "Advisory ranking:</strong> 0.90 (not a probability)" in document
    assert "Context-based suggestion" in document
    assert "A save <script>bad()</script>" not in document
    assert 'type="video/webm"' in document
    assert "source_reference: sourceReference" in document


def _run_review_js(document: str, actions: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute generated review interactions")
    script_match = re.search(r"<script>\s*(.*?)\s*</script>", document, re.DOTALL)
    assert script_match is not None
    harness = r"""
const vm = require('node:vm');
const controls = {};
for (const name of ['include', 'review-rating', 'rejection-reason',
 'category', 'title', 'start', 'end', 'peak', 'notes', 'window-error']) {
  controls[name] = {value: '', checked: false, events: {},
   addEventListener(event, fn) {this.events[event] = fn;}};
}
controls['review-rating'].value = 'unreviewed';
controls['rejection-reason'].value = 'unset';
controls.category.value = 'clutch'; controls.title.value = 'Edited title';
controls.start.value = '10'; controls.end.value = '50'; controls.notes.value = 'Human note';
controls.peak.value = '30';
const card = {dataset: {id: 'h1'}, querySelector(name) {return controls[name.slice(1)];}};
const download = {addEventListener(name, fn) {this[name] = fn;}};
let exported;
const document = {
 querySelectorAll(selector) {return selector === '.candidate' ? [card] : [];},
 querySelector() {return download;}, createElement() {return {click() {}};}
};
const context = {document, Blob: class {constructor(parts) {exported = JSON.parse(parts[0]);}},
 URL: {createObjectURL() {return 'blob:test';}, revokeObjectURL() {}}};
vm.runInNewContext(SCRIPT, context);
function change(name, value) {
 if (name === 'include') controls[name].checked = value; else controls[name].value = value;
 controls[name].events.change();
}
ACTIONS
download.click();
process.stdout.write(JSON.stringify(exported || {
 download_blocked:true, error:controls['window-error'].textContent,
 include_unchanged:controls.include.checked, rating_unchanged:controls['review-rating'].value
}));
"""
    harness = harness.replace("SCRIPT", json.dumps(script_match.group(1)))
    harness = harness.replace("ACTIONS", actions)
    result = subprocess.run(
        [node], input=harness, text=True, capture_output=True, check=True, encoding="utf-8"
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("actions", "rating", "include", "reason"),
    [
        ("", "unreviewed", False, "unset"),
        ("change('review-rating', 'keep');", "keep", False, "unset"),
        ("change('include', true);", "keep", True, "unset"),
        ("change('include', true); change('review-rating', 'reject');", "reject", False, "unset"),
        (
            "change('include', true); change('rejection-reason', 'wrong_timing');",
            "reject",
            False,
            "wrong_timing",
        ),
        ("change('include', true); change('include', false);", "unreviewed", False, "unset"),
        ("change('include', true); change('review-rating', 'maybe');", "maybe", False, "unset"),
    ],
)
def test_export_distinguishes_rating_from_deliberate_approval(
    tmp_path: Path, actions: str, rating: str, include: bool, reason: str
) -> None:
    exported = _run_review_js(_page(tmp_path), actions)
    item = exported["highlights"][0]
    assert item["review_rating"] == rating
    assert item["include"] is include
    assert item["rejection_reason"] == reason
    assert item["title"] == "Edited title"
    assert item["notes"] == "Human note"
    assert item["setup_seconds"] == 12.0
    assert item["payoff_seconds"] == 37.0
    assert exported["source_reference"] == str((tmp_path / "index.html").resolve())
    assert exported["author"] == exported["last_modified_by"] == "Neil Mitchell"
    HighlightCandidate.model_validate(item)


def test_human_trim_clears_excluded_machine_anchors_and_preserves_approval(tmp_path: Path) -> None:
    exported = _run_review_js(
        _page(tmp_path),
        "change('include', true); controls.start.value = '20'; controls.end.value = '35';",
    )
    item = exported["highlights"][0]
    assert item["start_seconds"] == 20
    assert item["end_seconds"] == 35
    assert item["peak_seconds"] == 30
    assert item["setup_seconds"] is None
    assert item["payoff_seconds"] is None
    assert item["include"] is True
    assert item["review_rating"] == "keep"
    HighlightCandidate.model_validate(item)


def test_trimmed_peak_blocks_download_until_human_updates_peak(tmp_path: Path) -> None:
    actions = "change('include', true); controls.start.value = '32'; controls.end.value = '40';"
    blocked = _run_review_js(_page(tmp_path), actions)
    assert blocked["download_blocked"] is True
    assert "place Peak seconds inside" in blocked["error"]
    assert blocked["include_unchanged"] is True
    assert blocked["rating_unchanged"] == "keep"
    exported = _run_review_js(_page(tmp_path), actions + " controls.peak.value = '35';")
    item = exported["highlights"][0]
    assert item["peak_seconds"] == 35
    assert item["setup_seconds"] is None
    assert item["payoff_seconds"] == 37
    assert item["include"] is True
    HighlightCandidate.model_validate(item)


def test_blind_comparison_deduplicates_windows_and_preserves_variant_identity(
    tmp_path: Path,
) -> None:
    first = HighlightCandidate(
        id="h1",
        title="Revealing baseline title",
        start_seconds=10,
        end_seconds=50,
        peak_seconds=30,
        category="clutch",
        score=1,
        include=True,
    )
    duplicate = first.model_copy(update={"id": "semantic-1", "title": "Revealing model title"})
    different = first.model_copy(
        update={
            "id": "semantic-2",
            "start_seconds": 60.0,
            "end_seconds": 100.0,
            "peak_seconds": 80.0,
        }
    )
    variants = {
        "baseline": ([first], {first.id: tmp_path / "baseline" / "h1.webm"}),
        "semantic": (
            [duplicate, different],
            {
                candidate.id: tmp_path / "semantic" / f"{candidate.id}.webm"
                for candidate in (duplicate, different)
            },
        ),
    }
    destination = tmp_path / "comparison.html"
    manifest = generate_highlight_comparison_page(
        variants,
        destination,
        source_reference="same-verified-recording",
        seed=17,
        includes_game=True,
        includes_discord=True,
        includes_microphone=True,
    )
    first_page = destination.read_text(encoding="utf-8")
    repeated = generate_highlight_comparison_page(
        variants,
        destination,
        source_reference="same-verified-recording",
        seed=17,
        includes_game=True,
        includes_discord=True,
        includes_microphone=True,
    )
    assert manifest == repeated
    assert first_page == destination.read_text(encoding="utf-8")
    assert manifest["unique_clip_count"] == 2
    assert manifest["variant_candidate_counts"] == {"baseline": 1, "semantic": 2}
    shared = next(clip for clip in manifest["clips"] if clip["start_seconds"] == 10.0)
    assert shared["members"] == [
        {"variant": "baseline", "candidate_id": "h1"},
        {"variant": "semantic", "candidate_id": "semantic-1"},
    ]
    visible = first_page.split("<script>")[0]
    assert "Revealing" not in first_page
    assert "baseline" not in re.sub(r'(?:src|href)="[^"]*"', "", visible)
    assert "Advisory ranking" not in visible
    assert 'type="checkbox"' not in visible
    assert "This page cannot approve or select an export" in visible
    assert "Candidate-only comparison cannot measure missed moments" in visible


def test_comparison_export_cannot_approve_even_with_positive_rating(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for generated comparison interactions")
    candidate = HighlightCandidate(
        id="already-approved",
        title="Original title",
        start_seconds=10,
        end_seconds=50,
        peak_seconds=30,
        category="reaction",
        score=0.9,
        include=True,
    )
    destination = tmp_path / "comparison.html"
    manifest = generate_highlight_comparison_page(
        {"baseline": ([candidate], {candidate.id: tmp_path / "a.webm"})},
        destination,
        source_reference="verified-source",
        includes_game=True,
        includes_discord=False,
        includes_microphone=False,
    )
    script = re.search(
        r"<script>\s*(.*?)\s*</script>", destination.read_text(encoding="utf-8"), re.DOTALL
    )
    assert script is not None
    harness = r"""
const vm = require('node:vm');
const rating = {value:'keep',addEventListener() {}};
const reason = {value:'unset',addEventListener() {}};
const video = {addEventListener() {},querySelector() {return {addEventListener() {}};}};
const card = {dataset:{id:IDENTITY}, querySelector(selector) {
  return selector === '.review-rating' ? rating :
    selector === '.rejection-reason' ? reason : video;
}};
const download = {addEventListener(name, fn) {this[name] = fn;}};
let exported;
const context = {
  document: {querySelectorAll() {return [card];},querySelector() {return download;},
    createElement() {return {click() {}};}},
  Blob:class {constructor(parts) {exported = JSON.parse(parts[0]);}},
  URL:{createObjectURL() {return 'blob:test';},revokeObjectURL() {}}
};
vm.runInNewContext(SCRIPT, context); download.click();
process.stdout.write(JSON.stringify(exported));
"""
    harness = harness.replace("IDENTITY", json.dumps(manifest["clips"][0]["id"]))
    harness = harness.replace("SCRIPT", json.dumps(script.group(1)))
    process = subprocess.run(
        [node], input=harness, capture_output=True, text=True, check=True, encoding="utf-8"
    )
    exported = json.loads(process.stdout)
    assert exported["highlights"][0]["review_rating"] == "keep"
    assert exported["highlights"][0]["include"] is False
    assert exported["clips"] == manifest["clips"]
    selection = tmp_path / "comparison-ratings.json"
    selection.write_text(json.dumps(exported), encoding="utf-8")
    feedback = record_editorial_feedback(selection, tmp_path / "feedback.json")
    assert feedback["rubric"]["counts"]["keep"] == 1
    assert feedback["rubric"]["examples"] == []
