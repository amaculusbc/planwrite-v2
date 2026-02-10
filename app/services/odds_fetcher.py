"""Odds fetcher service.

Fetches live odds from Charlotte/RotoGrinders API (ScoresAndOdds).
"""

from datetime import datetime
import re
from typing import Optional, Any

from app.config import get_settings
from app.services.http_utils import get_json

settings = get_settings()


class OddsFetcher:
    """Fetch odds data from Charlotte/RotoGrinders API."""

    # Sport mapping for Charlotte API endpoints
    SPORT_PATHS = {
        "nfl": "nfl",
        "ncaaf": "ncaaf",
        "ncaab": "ncaab",
        "nba": "nba",
        "mlb": "mlb",
        "nhl": "nhl",
    }

    # Sportsbook name mapping (friendly names)
    SPORTSBOOK_NAMES = {
        "draftkings": "DraftKings",
        "fanduel": "FanDuel",
        "betmgm": "BetMGM",
        "caesars": "Caesars",
        "bet365": "Bet365",
        "hardrock": "Hard Rock",
        "fanatics": "Fanatics",
        "espnbet": "ESPN BET",
    }

    # Brand to sportsbook key mapping
    BRAND_TO_SPORTSBOOK = {
        "draftkings": "draftkings",
        "fanduel": "fanduel",
        "betmgm": "betmgm",
        "caesars": "caesars",
        "bet365": "bet365",
        "hard rock": "hardrock",
        "fanatics": "fanatics",
        "espn bet": "espnbet",
    }

    # Sports that use weekly scheduling (football)
    WEEKLY_SPORTS = {"nfl", "ncaaf"}

    # Sports that use daily scheduling
    DAILY_SPORTS = {"nba", "mlb", "nhl", "ncaab"}

    def __init__(self, sport: str = "nfl"):
        """Initialize odds fetcher for a specific sport.

        Args:
            sport: Sport code (nfl, ncaaf, ncaab, nba, mlb, nhl)
        """
        self.sport = sport.lower()
        self.sport_path = self.SPORT_PATHS.get(self.sport, "nfl")
        self.base_url = f"https://charlotte.rotogrinders.com/sports/{self.sport_path}/extended"
        self.games_cache: list[dict] = []
        self.cache_timestamp: Optional[datetime] = None
        self.is_daily_sport = self.sport in self.DAILY_SPORTS
        self.api_key = settings.odds_api_key

    async def fetch_week_odds(self, week: str = "2025-reg-13") -> dict[str, Any]:
        """Fetch odds for an entire week (NFL/CFB only).

        Args:
            week: Week identifier (e.g., "2025-reg-13", "2025-post-1")

        Returns:
            Dict with full API response
        """
        if not self.api_key:
            print("Odds API key missing; set ODDS_API_KEY to enable odds fetching.")
            return {}
        params = {
            "role": "scoresandodds",
            "week": week,
            "key": self.api_key,
        }

        try:
            data = await get_json(self.base_url, params=params, timeout=10.0, retries=3)

            self.games_cache = data.get("data", [])
            self.cache_timestamp = datetime.now()
            return data

        except Exception as e:
            print(f"Error fetching week odds: {e}")
            return {}

    async def fetch_date_odds(self, target_date: datetime | None = None) -> dict[str, Any]:
        """Fetch odds for a specific date (NBA/MLB/NHL/CBB).

        Args:
            target_date: Date to fetch odds for (defaults to today)

        Returns:
            Dict with full API response
        """
        if not self.api_key:
            print("Odds API key missing; set ODDS_API_KEY to enable odds fetching.")
            return {}
        if target_date is None:
            target_date = datetime.now()

        date_str = target_date.strftime("%Y-%m-%d")

        params = {
            "role": "scoresandodds",
            "date": date_str,
            "key": self.api_key,
        }

        try:
            data = await get_json(self.base_url, params=params, timeout=10.0, retries=3)

            self.games_cache = data.get("data", [])
            self.cache_timestamp = datetime.now()
            return data

        except Exception as e:
            print(f"Error fetching date odds for {date_str}: {e}")
            return {}

    async def fetch_odds(
        self, week: str | None = None, target_date: datetime | None = None
    ) -> dict[str, Any]:
        """Unified method to fetch odds - automatically uses week or date based on sport.

        Args:
            week: Week identifier for NFL/CFB
            target_date: Date for NBA/MLB/NHL/CBB

        Returns:
            Dict with full API response
        """
        if self.is_daily_sport:
            return await self.fetch_date_odds(target_date)
        else:
            return await self.fetch_week_odds(week or "2025-reg-13")

    async def get_all_games(
        self, week: str = "2025-reg-13", target_date: datetime | None = None
    ) -> list[dict]:
        """Get list of all games with odds for a week/date."""
        if not self.games_cache:
            await self.fetch_odds(week=week, target_date=target_date)
        return self.games_cache or []

    async def find_game_by_teams(
        self,
        away_team: str,
        home_team: str,
        week: str | None = None,
        target_date: datetime | None = None,
    ) -> Optional[dict]:
        """Find a game by team names/keys.

        Args:
            away_team: Away team key (e.g., "KC", "GB", "Chiefs", "Packers")
            home_team: Home team key
            week: Week identifier for NFL/CFB
            target_date: Date for NBA/MLB/NHL/CBB

        Returns:
            Game dict if found, None otherwise
        """
        if not self.games_cache:
            await self.fetch_odds(week=week, target_date=target_date)

        def _normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

        def _match_team(input_str: str, team: dict) -> bool:
            s = _normalize(input_str)
            if not s:
                return False
            key = _normalize(team.get("key") or "")
            mascot = _normalize(team.get("mascot") or "")
            city = _normalize(team.get("city") or "")
            full = _normalize(f"{city} {mascot}".strip())
            tokens = set(s.split())

            if s in {key, mascot, city, full}:
                return True
            if key and key in tokens:
                return True
            if mascot and mascot in tokens:
                return True
            if city and city in tokens:
                return True
            if key and len(s) <= 4 and key.startswith(s):
                return True
            if key and len(key) <= 4 and s.startswith(key):
                return True
            return False

        for game in self.games_cache:
            away = game.get("away", {})
            home = game.get("home", {})

            if _match_team(away_team, away) and _match_team(home_team, home):
                return game

        return None

    @staticmethod
    def _coerce_american_odds(value: Any) -> Optional[int]:
        """Return odds as int when possible; otherwise None."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_spread_odds(
        self, game: dict, sportsbook: str = "draftkings"
    ) -> Optional[dict]:
        """Get spread odds for a specific sportsbook.

        Returns:
            Dict with spread odds: {
                'line': 2.5,
                'favorite': 'home',
                'away_odds': -105,
                'home_odds': -115,
                'away_team': 'Packers',
                'home_team': 'Lions'
            }
        """
        try:
            odds = game.get("odds", {}).get("current", {}).get("spread")
            if odds is None:
                return None
            comparison = odds.get("comparison", {})

            book_odds = comparison.get(sportsbook.lower(), odds)

            line = book_odds.get("value")
            favorite = book_odds.get("favorite")
            away_odds = self._coerce_american_odds(book_odds.get("away"))
            home_odds = self._coerce_american_odds(book_odds.get("home"))

            if line is None or favorite is None or away_odds is None or home_odds is None:
                return None

            return {
                "line": line,
                "favorite": favorite,
                "away_odds": away_odds,
                "home_odds": home_odds,
                "away_team": game["away"]["mascot"],
                "home_team": game["home"]["mascot"],
                "away_key": game["away"]["key"],
                "home_key": game["home"]["key"],
            }
        except (KeyError, TypeError):
            return None

    def get_moneyline_odds(
        self, game: dict, sportsbook: str = "draftkings"
    ) -> Optional[dict]:
        """Get moneyline odds for a specific sportsbook."""
        try:
            odds = game.get("odds", {}).get("current", {}).get("moneyline")
            if odds is None:
                return None
            comparison = odds.get("comparison", {})
            book_odds = comparison.get(sportsbook.lower(), odds)

            away_odds = self._coerce_american_odds(book_odds.get("away"))
            home_odds = self._coerce_american_odds(book_odds.get("home"))
            if away_odds is None or home_odds is None:
                return None

            return {
                "away_odds": away_odds,
                "home_odds": home_odds,
                "favorite": book_odds.get("favorite"),
                "away_team": game["away"]["mascot"],
                "home_team": game["home"]["mascot"],
                "away_key": game["away"]["key"],
                "home_key": game["home"]["key"],
            }
        except (KeyError, TypeError):
            return None

    def get_total_odds(
        self, game: dict, sportsbook: str = "draftkings"
    ) -> Optional[dict]:
        """Get over/under total odds for a specific sportsbook."""
        try:
            odds = game.get("odds", {}).get("current", {}).get("total")
            if odds is None:
                return None
            comparison = odds.get("comparison", {})
            book_odds = comparison.get(sportsbook.lower(), odds)

            line = book_odds.get("value")
            over_odds = self._coerce_american_odds(book_odds.get("over"))
            under_odds = self._coerce_american_odds(book_odds.get("under"))
            if line is None or over_odds is None or under_odds is None:
                return None

            return {
                "line": line,
                "over_odds": over_odds,
                "under_odds": under_odds,
                "favorite": book_odds.get("favorite"),
            }
        except (KeyError, TypeError):
            return None

    def format_spread_text(self, spread_odds: Optional[dict], sportsbook: str = "draftkings") -> str:
        """Format spread odds as human-readable text."""
        if not spread_odds:
            return "Odds unavailable"

        line = spread_odds["line"]
        favorite = spread_odds["favorite"]
        away_team = spread_odds["away_team"]
        home_team = spread_odds["home_team"]
        away_odds = spread_odds["away_odds"]
        home_odds = spread_odds["home_odds"]

        book_name = self.SPORTSBOOK_NAMES.get(sportsbook.lower(), sportsbook.title())

        if favorite == "home":
            return (
                f"{home_team} -{line} ({home_odds:+d}) vs "
                f"{away_team} +{line} ({away_odds:+d}) at {book_name}"
            )
        else:
            return (
                f"{away_team} -{line} ({away_odds:+d}) vs "
                f"{home_team} +{line} ({home_odds:+d}) at {book_name}"
            )

    def format_moneyline_text(self, ml_odds: Optional[dict], sportsbook: str = "draftkings") -> str:
        """Format moneyline odds as text."""
        if not ml_odds:
            return "Odds unavailable"

        book_name = self.SPORTSBOOK_NAMES.get(sportsbook.lower(), sportsbook.title())

        return (
            f"{ml_odds['home_team']} {ml_odds['home_odds']:+d} vs "
            f"{ml_odds['away_team']} {ml_odds['away_odds']:+d} at {book_name}"
        )

    def format_total_text(self, total_odds: Optional[dict], sportsbook: str = "draftkings") -> str:
        """Format over/under total as text."""
        if not total_odds:
            return "Odds unavailable"

        book_name = self.SPORTSBOOK_NAMES.get(sportsbook.lower(), sportsbook.title())

        return (
            f"Over/Under {total_odds['line']} "
            f"(O: {total_odds['over_odds']:+d} / U: {total_odds['under_odds']:+d}) "
            f"at {book_name}"
        )

    def get_all_odds_for_game(self, game: dict, sportsbook: str = "draftkings") -> dict[str, Any]:
        """Get all formatted odds for a game at a specific sportsbook.

        Returns:
            Dict with 'spread', 'moneyline', 'total' keys (formatted text)
            and 'spread_raw', 'moneyline_raw', 'total_raw' (raw data)
        """
        spread = self.get_spread_odds(game, sportsbook)
        moneyline = self.get_moneyline_odds(game, sportsbook)
        total = self.get_total_odds(game, sportsbook)

        return {
            "spread": self.format_spread_text(spread, sportsbook),
            "moneyline": self.format_moneyline_text(moneyline, sportsbook),
            "total": self.format_total_text(total, sportsbook),
            "spread_raw": spread,
            "moneyline_raw": moneyline,
            "total_raw": total,
        }

    def get_available_sportsbooks(self, game: dict) -> list[str]:
        """Get list of available sportsbooks for a game."""
        try:
            current = game.get("odds", {}).get("current", {})
            for market in ("spread", "moneyline", "total"):
                comparison = (current.get(market) or {}).get("comparison", {})
                if comparison:
                    return list(comparison.keys())

            if any(current.get(market) for market in ("spread", "moneyline", "total")):
                return ["consensus"]
            return []
        except (KeyError, TypeError):
            return []

    @classmethod
    def get_sportsbook_key(cls, brand: str) -> str:
        """Map brand name to sportsbook API key.

        Args:
            brand: Brand name (e.g., "DraftKings", "FanDuel")

        Returns:
            Sportsbook key for API (e.g., "draftkings")
        """
        brand_lower = brand.lower().strip()
        return cls.BRAND_TO_SPORTSBOOK.get(brand_lower, "draftkings")


def calculate_profit(bet_amount: float, odds: int) -> float:
    """Calculate potential profit from American odds.

    Args:
        bet_amount: Amount wagered
        odds: American odds (e.g., -110, +150)

    Returns:
        Potential profit (not including stake)
    """
    if odds is None:
        return 0.0

    try:
        if odds > 0:
            return (bet_amount * odds) / 100
        else:
            return (bet_amount * 100) / abs(odds)
    except (TypeError, ZeroDivisionError):
        return 0.0


def build_bet_options(game_odds: dict) -> list[dict]:
    """Build list of bet options from game odds.

    Args:
        game_odds: Dict from get_all_odds_for_game()

    Returns:
        List of bet option dicts with label, odds, type, selection
    """
    bet_options = []

    def fmt_odds(val: Optional[int]) -> str:
        try:
            return f"{int(val):+d}"
        except (TypeError, ValueError):
            return str(val) if val is not None else "N/A"

    spread_raw = game_odds.get("spread_raw")
    ml_raw = game_odds.get("moneyline_raw")
    total_raw = game_odds.get("total_raw")

    # Spread options
    if spread_raw:
        if spread_raw.get("favorite") == "home":
            bet_options.append({
                "label": f"{spread_raw['away_team']} +{spread_raw['line']} ({fmt_odds(spread_raw['away_odds'])})",
                "odds": spread_raw["away_odds"],
                "type": "spread",
                "selection": f"{spread_raw['away_team']} +{spread_raw['line']}",
            })
            bet_options.append({
                "label": f"{spread_raw['home_team']} -{spread_raw['line']} ({fmt_odds(spread_raw['home_odds'])})",
                "odds": spread_raw["home_odds"],
                "type": "spread",
                "selection": f"{spread_raw['home_team']} -{spread_raw['line']}",
            })
        else:
            bet_options.append({
                "label": f"{spread_raw['away_team']} -{spread_raw['line']} ({fmt_odds(spread_raw['away_odds'])})",
                "odds": spread_raw["away_odds"],
                "type": "spread",
                "selection": f"{spread_raw['away_team']} -{spread_raw['line']}",
            })
            bet_options.append({
                "label": f"{spread_raw['home_team']} +{spread_raw['line']} ({fmt_odds(spread_raw['home_odds'])})",
                "odds": spread_raw["home_odds"],
                "type": "spread",
                "selection": f"{spread_raw['home_team']} +{spread_raw['line']}",
            })

    # Moneyline options
    if ml_raw:
        bet_options.append({
            "label": f"{ml_raw['away_team']} Moneyline ({fmt_odds(ml_raw['away_odds'])})",
            "odds": ml_raw["away_odds"],
            "type": "moneyline",
            "selection": f"{ml_raw['away_team']} moneyline",
        })
        bet_options.append({
            "label": f"{ml_raw['home_team']} Moneyline ({fmt_odds(ml_raw['home_odds'])})",
            "odds": ml_raw["home_odds"],
            "type": "moneyline",
            "selection": f"{ml_raw['home_team']} moneyline",
        })

    # Total options
    if total_raw:
        bet_options.append({
            "label": f"Over {total_raw['line']} ({fmt_odds(total_raw['over_odds'])})",
            "odds": total_raw["over_odds"],
            "type": "total",
            "selection": f"Over {total_raw['line']}",
        })
        bet_options.append({
            "label": f"Under {total_raw['line']} ({fmt_odds(total_raw['under_odds'])})",
            "odds": total_raw["under_odds"],
            "type": "total",
            "selection": f"Under {total_raw['line']}",
        })

    return bet_options


def build_bet_example_text(
    bet_amount: float,
    selection: str,
    odds: int,
    event_context: str,
) -> str:
    """Build bet example text for prompts.

    Args:
        bet_amount: Amount wagered
        selection: Bet selection text (e.g., "Chiefs -3.5")
        odds: American odds
        event_context: Event description

    Returns:
        Formatted bet example text
    """
    profit = calculate_profit(bet_amount, odds)

    return (
        f"Suppose I place a ${bet_amount:.0f} first bet on the {selection} "
        f"in {event_context}:\n"
        f"* If the bet wins, I receive ${profit:.2f} in profit and my ${bet_amount:.0f} stake back.\n"
        f"* If the bet loses, [writer adds bonus bet details from terms]"
    )
