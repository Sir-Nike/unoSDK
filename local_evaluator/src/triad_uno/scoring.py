"""Event scoring for fixed-schedule UNO placement points.

Placements score 5, 3, 2, 1. At a turn limit, confirmed finishers keep their
placement points and unfinished seats share the points for the remaining places.
Standings use aggregate points, with the event's unique-rank tiebreaks. The
uncertainty estimate is clustered over four-game seat-rotation blocks.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import cast

POINTS = {0: 5.0, 1: 3.0, 2: 2.0, 3: 1.0}


def points_for_game(outcome: dict[str, object], seats: list[str]) -> dict[str, float]:
    """Return per-team placement points, including partial turn-limit draws."""
    placements = cast("list[int]", outcome.get("placements", []))
    unfinished = cast("list[int]", outcome.get("unfinished", []))
    if sorted(placements + unfinished) != list(range(len(seats))):
        raise ValueError("outcome must rank every seat exactly once")

    pts: dict[str, float] = {}
    for pos, seat in enumerate(placements):
        pts[seats[seat]] = POINTS[pos]
    if unfinished:
        remaining = [POINTS[pos] for pos in range(len(placements), len(seats))]
        shared = sum(remaining) / len(unfinished)
        for seat in unfinished:
            pts[seats[seat]] = shared
    return pts


def standings(results: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return unique tournament ranks under the participant rulebook."""
    total: dict[str, float] = defaultdict(float)
    games: dict[str, int] = defaultdict(int)
    first_place_wins: dict[str, int] = defaultdict(int)
    per_block: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    scored: list[tuple[dict[str, object], dict[str, float]]] = []

    for result in results:
        outcome = cast("dict[str, object]", result["outcome"])
        seats = cast("list[str]", result["seats"])
        pts = points_for_game(outcome, seats)
        scored.append((result, pts))
        placements = cast("list[int]", outcome.get("placements", []))
        if placements:
            first_place_wins[seats[placements[0]]] += 1
        for team, points in pts.items():
            total[team] += points
            games[team] += 1
            block = cast("str", result.get("block_id", result["game_id"]))
            per_block[block][team] += points

    rows: list[dict[str, object]] = []
    for team, value in total.items():
        mean = value / games[team]
        block_means = [
            per_block[block][team] / 4.0
            for block in per_block
            if team in per_block[block]
        ]
        se = _clustered_se(block_means) if len(block_means) > 1 else 0.0
        rows.append(
            {
                "team": team,
                "score": mean,
                "games": games[team],
                "total": value,
                "se": se,
                "blocks": len(block_means),
                "wins": first_place_wins[team],
            }
        )

    tiers: list[list[dict[str, object]]] = []
    for row in sorted(rows, key=lambda item: (-cast("float", item["total"]), cast("str", item["team"]))):
        if not tiers or row["total"] != tiers[-1][0]["total"]:
            tiers.append([row])
        else:
            tiers[-1].append(row)

    def victories_against(team: str, opponents: list[str]) -> int:
        victories = 0
        for result, points in scored:
            seats = cast("list[str]", result["seats"])
            if team not in seats:
                continue
            for opponent in opponents:
                if opponent in seats and points[team] > points[opponent]:
                    victories += 1
        return victories

    ranked: list[dict[str, object]] = []
    for index, tier in enumerate(tiers):
        if len(tier) > 1:
            if index == 0:
                reference_tier = tiers[1] if len(tiers) > 1 else []
            else:
                reference_tier = [ranked[-1]]
            reference_teams = [cast("str", row["team"]) for row in reference_tier]
            tier.sort(
                key=lambda row: (
                    -victories_against(cast("str", row["team"]), reference_teams),
                    -cast("int", row["wins"]),
                    cast("str", row["team"]),
                )
            )
        for row in tier:
            row["rank"] = len(ranked) + 1
            ranked.append(row)
    return ranked


def _clustered_se(block_means: list[float]) -> float:
    n = len(block_means)
    mean = sum(block_means) / n
    var = sum((x - mean) ** 2 for x in block_means) / (n - 1)
    return math.sqrt(var / n)
