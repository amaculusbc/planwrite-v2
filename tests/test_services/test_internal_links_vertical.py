"""Internal link ranking tests for sportsbook vs casino pages."""

from app.services.internal_links import InternalLinksStore


def test_get_operator_evergreen_link_prefers_sportsbook_review_over_casino_page():
    store = InternalLinksStore(property_key="action_network")
    link = store.get_operator_evergreen_link("DraftKings")
    assert link is not None
    assert "/online-sports-betting/" in link.url
    assert "/casino/" not in link.url
