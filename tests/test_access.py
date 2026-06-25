"""Reading the trusted Cloudflare Access identity off the request.

Access fronts every route and injects the authenticated email as a request header; the app trusts
it without verifying the signed JWT, justified by the no-public-origin network model. Off the edge
— local ``just run``, the test suite — the header is absent, and a missing identity must never turn
a working upload into an error, so the reader falls back to a neutral placeholder.
"""

from fishpage.access import UNKNOWN_UPLOADER, uploader_from_header


def test_a_present_header_is_credited_as_the_uploader():
    assert uploader_from_header("alice@example.com") == "alice@example.com"


def test_an_absent_header_falls_back_to_the_neutral_placeholder():
    # Off the Access edge the header is None; the upload still succeeds, credited to no real human.
    assert uploader_from_header(None) == UNKNOWN_UPLOADER


def test_a_blank_header_is_treated_as_absent():
    # An empty or whitespace-only value carries no identity, so it falls back rather than crediting
    # a blank Uploader.
    assert uploader_from_header("") == UNKNOWN_UPLOADER
    assert uploader_from_header("   ") == UNKNOWN_UPLOADER
