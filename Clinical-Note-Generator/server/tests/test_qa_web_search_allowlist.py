"""Regression test (P1-5): the QA web-search source allowlist must match
the parsed hostname, not do a substring check against the full URL.

Before this fix, _allowed(url) did `any(d in url.lower() for d in
_ALLOWED_DOMAINS)` -- a URL like "https://evil.example/who.int" or
"https://who.int.evil.example" would pass the "who.int" check even though
neither is actually the World Health Organization's site, because the
allowed domain merely appeared somewhere in the URL string (path or
subdomain prefix). Since these sources feed answers a clinician may treat
as coming from an authoritative source, this is not just a cosmetic bug.
"""
from server.services.qa_web_search import _allowed, _host_matches_entry


def test_real_domain_and_subdomain_are_allowed():
    assert _allowed("https://who.int/guidelines/x") is True
    assert _allowed("https://www.who.int/guidelines/x") is True
    assert _allowed("https://apps.who.int/iris/handle/123") is True


def test_domain_as_a_path_segment_is_not_allowed():
    assert _allowed("https://evil.example/who.int") is False
    assert _allowed("https://evil.example/path?ref=who.int") is False


def test_domain_as_a_subdomain_suffix_trick_is_not_allowed():
    # "who.int.evil.example" contains "who.int" as a substring but the
    # real registrable domain is evil.example.
    assert _allowed("https://who.int.evil.example/phish") is False


def test_case_insensitivity_matches_original_intent():
    # _ALLOWED_DOMAINS has "upToDate.com" (mixed case); the old substring
    # check against a *lowercased* URL could never match a mixed-case
    # entry, silently making it dead. Confirm it now actually works.
    assert _allowed("https://www.uptodate.com/contents/x") is True


def test_unrelated_domain_is_rejected():
    assert _allowed("https://example.com/") is False


def test_bare_keyword_entry_matches_hostname_substring_but_not_full_url():
    # "novonordisk" (no dot) is a brand-keyword entry, intentionally a
    # substring match -- but scoped to the hostname, not the whole URL.
    assert _host_matches_entry("www.novonordisk.com", "novonordisk") is True
    assert _host_matches_entry("novonordisk-trials.org", "novonordisk") is True
    assert _host_matches_entry("evil.example", "novonordisk") is False
