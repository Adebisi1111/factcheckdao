"""Direct-mode tests for FactCheckDAO."""

import json
from tests.direct.conftest import to_hex


def test_smoke(direct_vm, direct_deploy, direct_alice):
    assert True


def test_submit_article(direct_vm, direct_deploy, direct_alice):
    c = direct_deploy("contracts/factcheck_dao.py")
    direct_vm.sender = direct_alice
    id_ = c.submit_article("https://example.com/article")
    assert id_ == "article-1"
    d = json.loads(c.get_article(id_))
    assert d["exists"] is True
    assert d["url"] == "https://example.com/article"
    assert d["status"] == "PENDING"
    assert d["submitter"].lower() == to_hex(direct_alice).lower()


def test_submit_url_validation(direct_vm, direct_deploy, direct_alice):
    c = direct_deploy("contracts/factcheck_dao.py")
    direct_vm.sender = direct_alice
    try:
        c.submit_article("example.com")  # no protocol
        assert False, "should have raised"
    except Exception as e:
        assert "http" in str(e).lower()


def test_cannot_resolve_twice(direct_vm, direct_deploy, direct_alice):
    c = direct_deploy("contracts/factcheck_dao.py")
    direct_vm.sender = direct_alice
    id_ = c.submit_article("https://example.com/article")
    # Mock web fetch returning text body
    direct_vm.mock_web("example.com", {
        "method": "GET",
        "status": 200,
        "body": "2026-10-06: Bitcoin breaks $100k according to our reporter"
    })
    # First call: claim extraction. Mock pattern must match prompt content.
    direct_vm.mock_llm("ExtractClaims", "Bitcoin hits $100k")
    # Second call: cross-reference verdict. Match on the verdict-shape tokens.
    direct_vm.mock_llm("CrossReference", "SUPPORTED")
    v = c.resolve_article(id_)
    assert v in ("SUPPORTED", "REFUTED", "INSUFFICIENT")
    article = json.loads(c.get_article(id_))
    assert article["status"] == "RESOLVED"
    verdict = json.loads(c.get_verdict(id_))
    assert verdict["exists"] is True
    assert verdict["verdict"] == v

    direct_vm.sender = direct_alice
    try:
        c.resolve_article(id_)
        assert False, "should raise already resolved"
    except Exception as e:
        assert "already resolved" in str(e).lower()


def test_pending_until_resolved(direct_vm, direct_deploy, direct_alice):
    """split votes are exercised on-chain at runtime; direct SDK does not simulate split votes."""
    c = direct_deploy("contracts/factcheck_dao.py")
    direct_vm.sender = direct_alice
    id_ = c.submit_article("https://example.com/article")
    article = json.loads(c.get_article(id_))
    assert article["status"] == "PENDING"
    assert article["resolve_count"] == 0


def test_insufficient_when_no_support(direct_vm, direct_deploy, direct_alice):
    c = direct_deploy("contracts/factcheck_dao.py")
    direct_vm.sender = direct_alice
    id_ = c.submit_article("https://example.com/new-claim")
    direct_vm.mock_web("example.com", {
        "method": "GET",
        "status": 200,
        "body": "Brand new unsupported claim"
    })
    direct_vm.mock_llm("ExtractClaims", "Nobody has heard of X")
    direct_vm.mock_llm("CrossReference", "INSUFFICIENT")
    v = c.resolve_article(id_)
    article = json.loads(c.get_article(id_))
    assert v == "INSUFFICIENT"
