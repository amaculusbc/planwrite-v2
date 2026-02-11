"""Event/Game fetcher service.

Fetches games from ESPN API for various sports.
"""

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.services.http_utils import get_json

SPORT_PATHS = {
    "nfl": "football/nfl",
    "nba": "basketball/nba",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "ncaaf": "football/college-football",
    "ncaab": "basketball/mens-college-basketball",
}

SPORT_LABELS = {
    "nfl": "NFL",
    "nba": "NBA",
    "mlb": "MLB",
    "nhl": "NHL",
    "ncaaf": "CFB",
    "ncaab": "CBB",
}


async def get_games_for_date(sport: str = "nfl", target_date: datetime | None = None) -> list[dict]:
    """Fetch all games for a specific date from ESPN API.

    Args:
        sport: Sport code (nfl, nba, mlb, nhl, ncaaf, ncaab)
        target_date: Date to fetch games for (defaults to today ET)

    Returns:
        List of game dicts with home_team, away_team, start_time, etc.
    """
    sport_path = SPORT_PATHS.get(sport.lower())
    if not sport_path:
        return []

    if target_date is None:
        target_date = datetime.now(ZoneInfo("America/New_York"))

    date_str = target_date.strftime("%Y%m%d")
    url = f"http://site.api.espn.com/apis/site/v2/sports/{sport_path}/scoreboard?dates={date_str}"

    try:
        data = await get_json(url, timeout=10.0, retries=3)

        events = data.get("events", [])
        if not events:
            return []

        games = []
        for game in events:
            competitions = game.get("competitions", [{}])[0]
            competitors = competitions.get("competitors", [])

            if len(competitors) < 2:
                continue

            # Find home/away
            home = competitors[0] if competitors[0].get("homeAway") == "home" else competitors[1]
            away = competitors[1] if competitors[0].get("homeAway") == "home" else competitors[0]

            # Get broadcast info
            broadcasts = competitions.get("broadcasts", [])
            network = broadcasts[0].get("names", [""])[0] if broadcasts else ""

            # Parse game time
            game_time = game.get("date", "")
            dt_et = None
            try:
                dt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
                dt_et = dt.astimezone(ZoneInfo("America/New_York"))
            except Exception:
                pass

            # Extract week/season metadata when available (football)
            week_info = game.get("week", {}) or competitions.get("week", {})
            season_info = game.get("season", {}) or competitions.get("season", {})
            week_num = None
            season_type = None
            season_year = None
            try:
                week_num = week_info.get("number")
            except Exception:
                week_num = None
            try:
                season_type = season_info.get("type") or season_info.get("type", {}).get("name")
            except Exception:
                season_type = None
            try:
                season_year = season_info.get("year")
            except Exception:
                season_year = None

            games.append({
                "id": game.get("id", ""),
                "home_team": home.get("team", {}).get("displayName", ""),
                "away_team": away.get("team", {}).get("displayName", ""),
                "home_abbrev": home.get("team", {}).get("abbreviation", ""),
                "away_abbrev": away.get("team", {}).get("abbreviation", ""),
                "start_time": game_time,
                "start_time_et": dt_et,
                "network": network,
                "headline": game.get("name", ""),
                "short_name": game.get("shortName", ""),
                "sport": sport.upper(),
                "week": week_num,
                "season_type": season_type,
                "season_year": season_year,
            })

        # Sort by start time
        games.sort(key=lambda g: g.get("start_time_et") or datetime.min.replace(tzinfo=ZoneInfo("UTC")))
        return games

    except Exception as e:
        print(f"Failed to fetch {sport} games: {e}")
        return []


def filter_prime_time_games(games: list[dict]) -> list[dict]:
    """Filter for prime time games (evening games after 6 PM ET).

    Args:
        games: List of game dicts

    Returns:
        List of prime time games, sorted by time (latest first)
    """
    if not games:
        return []

    prime_time = []
    for game in games:
        dt_et = game.get("start_time_et")
        if not dt_et:
            continue

        # Prime time = 6 PM or later (18:00+)
        if dt_et.hour >= 18:
            prime_time.append(game)

    # Sort by time (latest first for most premium slots)
    prime_time.sort(key=lambda g: g.get("start_time_et", datetime.min), reverse=True)
    return prime_time


async def get_featured_game(sport: str = "nfl", target_date: datetime | None = None) -> Optional[dict]:
    """Fetch featured game (prime time preferred) for a sport on a specific date.

    Args:
        sport: Sport code
        target_date: Target date

    Returns:
        Featured game dict or None
    """
    games = await get_games_for_date(sport, target_date)

    if not games:
        return None

    # Try to get prime time game first
    prime_games = filter_prime_time_games(games)
    if prime_games:
        return prime_games[0]

    # Fallback to first game of the day
    return games[0]


def format_event_for_prompt(game: Optional[dict], reference_date: datetime | None = None) -> str:
    """Format game data into natural text for prompts.

    Args:
        game: Game dict from get_games_for_date
        reference_date: Reference date for relative day context

    Returns:
        Formatted string like "Chiefs vs. Ravens tonight at 8:15 PM ET on ESPN"
    """
    if not game:
        return ""

    try:
        dt_et = game.get("start_time_et")
        if not dt_et:
            return f"{game.get('away_team', '')} vs. {game.get('home_team', '')}"

        # Determine day context relative to reference date
        if reference_date is None:
            reference_date = datetime.now(ZoneInfo("America/New_York"))

        ref_date = reference_date.date()
        game_date = dt_et.date()

        if game_date == ref_date:
            day_context = "tonight"
        elif game_date == (ref_date + timedelta(days=1)):
            day_context = "tomorrow night"
        else:
            day_context = dt_et.strftime("%A night")

        time_str = dt_et.strftime("%I:%M %p ET").lstrip("0")

        # Determine if it's a marquee game
        hour = dt_et.hour
        marquee = None
        if hour >= 20 and game.get("sport") == "NFL":
            weekday = dt_et.weekday()
            if weekday == 6:  # Sunday
                marquee = "Sunday Night Football"
            elif weekday == 0:  # Monday
                marquee = "Monday Night Football"
            elif weekday == 3:  # Thursday
                marquee = "Thursday Night Football"

        # Build context string
        parts = [f"{game['away_team']} vs. {game['home_team']}"]

        if marquee:
            parts.append(f"on {marquee}")

        parts.append(day_context)

        if game.get('network'):
            parts.append(f"at {time_str} on {game['network']}")
        else:
            parts.append(f"at {time_str}")

        return " ".join(parts)

    except Exception as e:
        print(f"Failed to format event: {e}")
        return f"{game.get('away_team', '')} vs. {game.get('home_team', '')}"


def format_game_for_dropdown(game: dict) -> str:
    """Format game for display in dropdown selector.

    Args:
        game: Game dict

    Returns:
        Formatted string like "Chiefs @ Ravens - 8:15 PM ET (ESPN)"
    """
    dt_et = game.get("start_time_et")
    if dt_et:
        time_str = dt_et.strftime("%I:%M %p ET").lstrip("0")
        return f"{game['away_team']} @ {game['home_team']} - {time_str} ({game.get('network', 'TBD')})"
    return f"{game['away_team']} @ {game['home_team']}"


def format_game_start_time(game: dict) -> str:
    """Format game start time as an ET date/time display string."""
    dt_et = game.get("start_time_et")
    if not dt_et:
        start_time = game.get("start_time")
        if start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                dt_et = dt.astimezone(ZoneInfo("America/New_York"))
            except Exception:
                return ""

    if not dt_et:
        return ""

    hour = dt_et.strftime("%I").lstrip("0") or "12"
    return (
        f"{dt_et.strftime('%a')}, {dt_et.strftime('%b')} {dt_et.day}, "
        f"{hour}:{dt_et.strftime('%M')} {dt_et.strftime('%p')} ET"
    )


def get_available_sports() -> dict[str, str]:
    """Get available sports with their labels.

    Returns:
        Dict mapping sport code to display label
    """
    return SPORT_LABELS.copy()
