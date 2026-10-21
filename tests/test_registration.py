"""Tests for registration number validation - wired to the REAL rule.

Historical note (H-03): this file previously tested a hardcoded
``^\\d{8,12}$`` pattern that has nothing to do with the shipped rule
(``settings.registration_no_pattern``, default ``^[A-Za-z0-9_-]{6,12}$``),
so it could not fail for any defect in the actual validator.  It now
compiles the configured pattern and exercises ``_validate_reg_no`` itself.

All values below are synthetic - never real registration numbers (PII scan).
"""

from __future__ import annotations

import re

import pytest

from app.config import settings
from app.services.enrollment import EnrollmentError, _validate_reg_no

VALID = ["STU100001", "902250001", "AB_12-3", "21CSE002", "STU123", "X" * 12]
INVALID = ["ABC", "TOOLONG1234567", "BAD SP", "BAD!@#", "", "A" * 13]


def test_configured_pattern_is_usable():
    assert settings.registration_no_pattern
    compiled = re.compile(settings.registration_no_pattern)
    assert compiled.pattern == settings.registration_no_pattern


def test_valid_registration_numbers():
    compiled = re.compile(settings.registration_no_pattern)
    for rn in VALID:
        assert compiled.match(rn), f"{rn!r} should be valid"


def test_invalid_registration_numbers():
    compiled = re.compile(settings.registration_no_pattern)
    for rn in INVALID:
        assert not compiled.match(rn), f"{rn!r} should be invalid"


def test_validate_reg_no_accepts_and_rejects_like_the_pattern():
    for rn in ("STU100001", "21CSE002"):
        _validate_reg_no(rn)  # must not raise
    for rn in ("bad!", "short"):
        with pytest.raises(EnrollmentError):
            _validate_reg_no(rn)
