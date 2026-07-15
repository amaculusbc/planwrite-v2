"""BC Core market -> odds mapping (fixtures mirror real /events/{id}/markets payloads)."""

from app.services.bc_core_odds import _is_us_book, map_markets_to_odds
from app.services.goal_template import format_odds_talking_points

AWAY_ID = 190058
HOME_ID = 198398


def _market(bet_type, line_type, outcomes, period="Game", is_live=False):
    return {
        "betType": {"name": bet_type},
        "lineType": {"name": line_type},
        "linePeriodType": {"name": period},
        "isLive": is_live,
        "outcomes": outcomes,
    }


def test_maps_soccer_1x2_with_draw_price():
    markets = [
        _market("1X2", "Moneyline", [
            {"teamId": AWAY_ID, "optionType": None, "americanOdds": 132},
            {"teamId": HOME_ID, "optionType": None, "americanOdds": 185},
            {"teamId": None, "optionType": "Draw", "americanOdds": 210},
        ]),
    ]
    odds = map_markets_to_odds(markets, away_team_id=AWAY_ID, home_team_id=HOME_ID, book_label="bet365")
    assert odds["moneylines"]["bet365"] == {"away_odds": 132, "home_odds": 185, "draw_odds": 210}


def test_maps_totals_and_spreads():
    markets = [
        _market("Matchup", "Total", [
            {"teamId": None, "optionType": "Over", "americanOdds": 115, "line": 2.5},
            {"teamId": None, "optionType": "Under", "americanOdds": -150, "line": 2.5},
        ]),
        _market("Matchup", "Spread", [
            {"teamId": AWAY_ID, "americanOdds": -160, "line": 0.5},
            {"teamId": HOME_ID, "americanOdds": 135, "line": -0.5},
        ]),
    ]
    odds = map_markets_to_odds(markets, away_team_id=AWAY_ID, home_team_id=HOME_ID, book_label="bet365")
    assert odds["totals"]["bet365"] == {"over_odds": 115, "under_odds": -150, "total": 2.5}
    assert odds["spreads"]["bet365"]["away_line"] == "+0.5"
    assert odds["spreads"]["bet365"]["home_line"] == "-0.5"


def test_prefers_1x2_over_two_way_moneyline_so_the_draw_survives():
    markets = [
        _market("Matchup", "Moneyline", [
            {"teamId": AWAY_ID, "americanOdds": -150},
            {"teamId": HOME_ID, "americanOdds": 110},
        ]),
        _market("1X2", "Moneyline", [
            {"teamId": AWAY_ID, "americanOdds": 132},
            {"teamId": HOME_ID, "americanOdds": 185},
            {"teamId": None, "optionType": "Draw", "americanOdds": 210},
        ]),
    ]
    odds = map_markets_to_odds(markets, away_team_id=AWAY_ID, home_team_id=HOME_ID, book_label="bet365")
    assert odds["moneylines"]["bet365"]["draw_odds"] == 210
    assert odds["moneylines"]["bet365"]["away_odds"] == 132


def test_ignores_live_and_non_game_period_markets():
    markets = [
        _market("1X2", "Moneyline", [
            {"teamId": AWAY_ID, "americanOdds": 999},
            {"teamId": HOME_ID, "americanOdds": 999},
        ], period="1st Half"),
        _market("1X2", "Moneyline", [
            {"teamId": AWAY_ID, "americanOdds": 888},
            {"teamId": HOME_ID, "americanOdds": 888},
        ], is_live=True),
    ]
    assert map_markets_to_odds(markets, away_team_id=AWAY_ID, home_team_id=HOME_ID, book_label="bet365") == {}


def test_us_book_filter_excludes_international_books():
    # A German book is what produced "Wetten-Konfigurator" copy.
    assert _is_us_book("bet365 NJ") is True
    assert _is_us_book("bet365 KY") is True
    assert _is_us_book("bet365 DEU") is False
    assert _is_us_book("bet365 Canada") is False
    assert _is_us_book("bet365 GBR") is False


def test_talking_points_render_the_soccer_draw_price():
    odds = {"moneylines": {"bet365": {"away_odds": 132, "home_odds": 185, "draw_odds": 210}}}
    points = format_odds_talking_points(odds, "Egypt", "Australia", preferred_book="bet365")

    assert "Match result (1X2): Egypt +132 / Draw +210 / Australia +185" in points[0]


def test_talking_points_stay_two_way_without_a_draw():
    odds = {"moneylines": {"bet365": {"away_odds": -130, "home_odds": 110}}}
    points = format_odds_talking_points(odds, "Mets", "Phillies", preferred_book="bet365")

    assert "Moneyline: Mets -130 / Phillies +110" in points[0]
    assert "Draw" not in points[0]
