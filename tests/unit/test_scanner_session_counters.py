"""Unit tests for OpportunityScanner per-session counters.

Counters power the UI's "本次会话机会数" + "拒绝原因分布" panel.
The legacy frontend used to count items in the bounded /opportunities/recent
response, which capped at the limit value (e.g. 10). Now the scanner tracks
real session totals which the API surfaces directly.
"""

from __future__ import annotations

from app.config.settings import Settings
from app.strategy.opportunity_scanner import OpportunityScanner


def _make_scanner() -> OpportunityScanner:
    settings = Settings()
    return OpportunityScanner(
        settings=settings,
        book_mgr=None,  # type: ignore[arg-type]
        balance_mgr=None,  # type: ignore[arg-type]
        calc=None,  # type: ignore[arg-type]
    )


def test_counters_start_at_zero():
    s = _make_scanner()
    assert s.session_count() == 0
    assert s.accepted_count() == 0
    assert s.reject_counts() == {}


def test_record_detected_increments_session():
    s = _make_scanner()
    s.record_detected()
    s.record_detected()
    s.record_detected()
    assert s.session_count() == 3
    # Detection alone does NOT increment accepted/rejected.
    assert s.accepted_count() == 0
    assert s.reject_counts() == {}


def test_record_decision_accepted_bumps_accepted_only():
    s = _make_scanner()
    s.record_decision(accepted=True)
    s.record_decision(accepted=True)
    assert s.accepted_count() == 2
    assert s.reject_counts() == {}


def test_record_decision_rejected_bumps_reason_bucket():
    s = _make_scanner()
    s.record_decision(accepted=False, reason="below_min_edge")
    s.record_decision(accepted=False, reason="below_min_edge")
    s.record_decision(accepted=False, reason="below_min_size")
    assert s.accepted_count() == 0
    assert s.reject_counts() == {"below_min_edge": 2, "below_min_size": 1}


def test_record_decision_missing_reason_falls_to_unknown():
    s = _make_scanner()
    s.record_decision(accepted=False, reason=None)
    assert s.reject_counts() == {"unknown": 1}


def test_reject_counts_is_a_copy_not_a_live_view():
    s = _make_scanner()
    s.record_decision(accepted=False, reason="cooldown_active")
    snapshot = s.reject_counts()
    snapshot["cooldown_active"] = 999  # tamper
    # Internal state untouched
    assert s.reject_counts() == {"cooldown_active": 1}


def test_full_funnel_consistency():
    """detected = accepted + sum(rejects) for a closed loop scenario."""
    s = _make_scanner()
    for _ in range(5):
        s.record_detected()
    s.record_decision(accepted=True)
    s.record_decision(accepted=True)
    s.record_decision(accepted=False, reason="below_min_edge")
    s.record_decision(accepted=False, reason="below_min_edge")
    s.record_decision(accepted=False, reason="market_data_stale")
    rejected_total = sum(s.reject_counts().values())
    assert s.session_count() == 5
    assert s.accepted_count() + rejected_total == 5
