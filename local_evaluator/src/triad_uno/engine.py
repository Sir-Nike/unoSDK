"""Deterministic Triad UNO v1 engine.

API:
    new_game(config, seed) -> GameState
    observe(state, seat) -> Observation
    legal_actions(state) -> list[EngineAction]
    step(state, action) -> Transition

Trust boundary: this module owns the rules. The runner must not duplicate them.
No Docker / DB / ML dependencies. Only stdlib.
"""

from __future__ import annotations

import copy
import math
import random
import secrets
from dataclasses import dataclass, field

from .cards import (
    COLOR_INDEX,
    COLORS,
    COLOURED_ACTIONS,
    DRAW_TWO,
    NUMBER,
    REVERSE,
    SKIP,
    WILD,
    WILD_DRAW_FOUR,
    Card,
    build_canonical_deck,
    most_common_color_first,
)

RULESET_VERSION = "triad-uno-v1"

JsonDict = dict[str, object]

SEATS = 4

SETUP = "SETUP"
CHOOSE_INITIAL_COLOR = "CHOOSE_INITIAL_COLOR"
TURN = "TURN"
AFTER_DRAW = "AFTER_DRAW"
FINISHED = "FINISHED"


@dataclass
class GameConfig:
    """Game rules knobs that affect one game's termination."""

    max_turns: int = 1000
    ruleset_version: str = RULESET_VERSION
    initial_hand_size: int = 7
    position_points: tuple[float, ...] | None = None

    @property
    def players(self) -> int:
        """Read-only event table size backed by the ``SEATS`` constant."""
        return SEATS

    def __post_init__(self) -> None:
        if type(self.initial_hand_size) is not int or self.initial_hand_size < 1:
            raise ValueError("initial_hand_size must be a positive integer")
        # Reserve five cards: four could be WD4, which cannot start the discard.
        if self.players * self.initial_hand_size > 103:
            raise ValueError("one 108-card deck must retain at least five cards to guarantee a legal initial discard")
        if type(self.max_turns) is not int or self.max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        if self.ruleset_version != RULESET_VERSION:
            raise ValueError("unsupported UNO ruleset")
        if self.position_points is None:
            self.position_points = (5., 3., 2., 1.)
        self.position_points = tuple(self.position_points)
        if len(self.position_points) != self.players or any(
            type(p) not in (int, float) or not math.isfinite(p) or p < 0
            for p in self.position_points
        ):
            raise ValueError("position_points must contain one finite nonnegative number per player")
        if any(a < b for a, b in zip(self.position_points, self.position_points[1:])):
            raise ValueError("position_points must be non-increasing")
        if not math.isfinite(sum(self.position_points)):
            raise ValueError("position_points total must be finite")

    def to_dict(self) -> JsonDict:
        return {"max_turns": self.max_turns, "players": self.players,
                "initial_hand_size": self.initial_hand_size,
                "position_points": list(self.position_points), "ruleset_version": self.ruleset_version}


@dataclass
class Outcome:
    """Placement / draw outcome for a finished game."""

    placements: list[int]  # finish order; [] on pure draw; prefix on partial draw
    is_draw: bool
    unfinished: list[int]
    reason: str  # "all_placed" | "turn_limit_empty" | "turn_limit_partial"


@dataclass
class GameState:
    """Full mutable game state owned by the trusted engine."""

    config: GameConfig
    seed: int
    rng: random.Random
    hands: list[list[Card]]  # 4 seats
    draw_pile: list[Card]  # top at end (pop())
    discard_pile: list[Card]  # top at end ([-1])
    current_seat: int
    direction: int  # +1 clockwise, -1 counter-clockwise
    active_color: str | None
    phase: str
    # A positive value means the current seat is answering an unresolved
    # Draw Two / Wild Draw Four chain.  The seat may add a permitted
    # multiplier or accept the whole penalty with a ``draw`` action.
    pending_penalty: int = 0
    pending_source: int | None = None
    drawn_card_uid: int | None = None
    turn_count: int = 0
    finished_order: list[int] = field(default_factory=list)
    finished_set: set[int] = field(default_factory=set)
    outcome: Outcome | None = None
    history: list[JsonDict] = field(default_factory=list)
    initial_seed_setup: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class EngineAction:
    """One engine-level action (uid-based, deterministic, replay-safe).

    Types are play, draw, keep, and choose_color. In ``TURN``, ``draw`` is
    either the restricted one-card draw or acceptance of a pending penalty.
    """

    type: str
    card_uid: int | None = None
    color: str | None = None

    def to_dict(self) -> JsonDict:
        """Return the action as a plain JSON-serializable dict."""
        return {"type": self.type, "card_uid": self.card_uid, "color": self.color}


@dataclass
class Transition:
    """Result of one step: events, outcome, and optional error."""

    events: list[JsonDict]
    outcome: Outcome | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the step succeeded (no error)."""
        return self.error is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _hand_sizes(state: GameState) -> list[int]:
    return [len(h) for h in state.hands]


def _find_card(hand: list[Card], uid: int) -> Card | None:
    for c in hand:
        if c.uid == uid:
            return c
    return None


def _has_active_color(hand: list[Card], active_color: str | None) -> bool:
    if active_color is None:
        return False
    return any(c.color == active_color for c in hand)


def is_card_playable(
    card: Card,
    top: Card,
    active_color: str | None,
    hand: list[Card],
) -> bool:
    """Single-card legality given the full hand (needed for WD4 rule)."""
    if card.kind == WILD:
        return True
    if card.kind == WILD_DRAW_FOUR:
        return not _has_active_color(hand, active_color)
    # coloured cards
    if active_color is not None and card.color == active_color:
        return True
    if card.kind == NUMBER and top.kind == NUMBER and card.value == top.value:
        return True
    return card.kind in COLOURED_ACTIONS and top.kind == card.kind


def is_card_stackable(card: Card, top: Card, pending_penalty: int) -> bool:
    """Return whether ``card`` may continue an unresolved penalty chain.

    Draw Two can answer only Draw Two.  Wild Draw Four can answer either
    multiplier.  Stacking is an explicit exception to the normal Wild Draw
    Four colour restriction: the card is already being played as a penalty
    response, not as a fresh ordinary Wild Draw Four.
    """
    if pending_penalty <= 0:
        return False
    if card.kind == DRAW_TWO:
        return top.kind == DRAW_TWO
    if card.kind == WILD_DRAW_FOUR:
        return top.kind in (DRAW_TWO, WILD_DRAW_FOUR)
    return False


def _playable_cards(state: GameState, seat: int) -> list[Card]:
    hand = state.hands[seat]
    top = state.discard_pile[-1]
    return [c for c in hand if is_card_playable(c, top, state.active_color, hand)]


def _stackable_cards(state: GameState, seat: int) -> list[Card]:
    """Return this seat's cards that may continue the pending penalty."""
    if state.pending_penalty <= 0:
        return []
    top = state.discard_pile[-1]
    return [
        c
        for c in state.hands[seat]
        if is_card_stackable(c, top, state.pending_penalty)
    ]


def _next_unfinished(state: GameState, seat: int, direction: int | None = None) -> int:
    d = state.direction if direction is None else direction
    for _ in range(state.config.players):
        seat = (seat + d) % state.config.players
        if seat not in state.finished_set:
            return seat
    msg = "no unfinished seats (game should have ended)"
    raise RuntimeError(msg)


def _remaining_unfinished(state: GameState) -> list[int]:
    return [s for s in range(state.config.players) if s not in state.finished_set]


def _base_event(state: GameState, etype: str, seat: int | None) -> JsonDict:
    return {
        "type": etype,
        "turn_number": state.turn_count + 1,  # 1-indexed current turn
        "seat": seat,
        "hand_sizes": _hand_sizes(state),
    }


def draw_cards(state: GameState, seat: int, n: int) -> int:
    """Draw up to n cards into seat's hand, recycling as needed. Returns # drawn."""
    drawn = 0
    for _ in range(n):
        if not state.draw_pile:
            _recycle(state)
        if not state.draw_pile:
            break
        state.hands[seat].append(state.draw_pile.pop())
        drawn += 1
    return drawn


def _recycle(state: GameState) -> None:
    if len(state.discard_pile) <= 1:
        return
    top = state.discard_pile[-1]
    pool = state.discard_pile[:-1]
    state.rng.shuffle(pool)
    state.discard_pile = [top]
    # pool is shuffled; top of draw pile is the end
    state.draw_pile = pool


# ---------------------------------------------------------------------------
# canonical ordering (fallback + a0)
# ---------------------------------------------------------------------------

_KIND_RANK = {NUMBER: 0, SKIP: 1, REVERSE: 2, DRAW_TWO: 3, WILD: 4, WILD_DRAW_FOUR: 5}


def _card_category(card: Card) -> int:
    if card.kind == NUMBER:
        return 0
    if card.kind in COLOURED_ACTIONS:
        return 1
    if card.kind == WILD:
        return 2
    return 3


def order_play_cards(cards: list[Card]) -> list[Card]:
    """Return cards in canonical play order (category, colour, value, uid)."""

    def key(c: Card) -> tuple[int, int, int, int]:
        return (
            _card_category(c),
            COLOR_INDEX.get(c.color or "", 99),
            c.value if c.value is not None else _KIND_RANK.get(c.kind, 99),
            c.uid,
        )

    return sorted(cards, key=key)


def expand_play_actions(playable: list[Card], hand: list[Card]) -> list[EngineAction]:
    """Expand playable cards into engine actions, canonically ordered.

    Wild / WD4 expand into 4 colour variants ordered by most-common hand
    colour first (ties → fixed colour order). Other cards yield one action.
    """
    ordered = order_play_cards(playable)
    actions: list[EngineAction] = []
    colour_pref = most_common_color_first(hand)
    for card in ordered:
        if card.kind in (WILD, WILD_DRAW_FOUR):
            actions.extend(
                EngineAction(type="play", card_uid=card.uid, color=col) for col in colour_pref
            )
        else:
            actions.append(EngineAction(type="play", card_uid=card.uid, color=None))
    return actions


def fallback_action(state: GameState) -> EngineAction | None:
    """Return the published phase-specific deterministic fallback."""
    legal = legal_actions(state)
    if not legal:
        return None
    if state.phase == AFTER_DRAW:
        # A faulty agent must keep the drawn card; it does not get to spend
        # the fallback on a second play decision.
        return EngineAction(type="keep")
    if state.phase == CHOOSE_INITIAL_COLOR:
        return EngineAction(
            type="choose_color", color=most_common_color_first(state.hands[state.current_seat])[0]
        )
    return legal[0]


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def _apply_initial_effects(state: GameState, initial: Card, selected: int) -> None:
    if initial.kind == NUMBER:
        state.current_seat = selected
        state.phase = TURN
    elif initial.kind == SKIP:
        nxt = _next_unfinished(state, selected)
        state.history.append(
            {**_base_event(state, "initial_skip", None), "skipped": selected, "next_seat": nxt}
        )
        state.current_seat = nxt
        state.phase = TURN
    elif initial.kind == DRAW_TWO:
        got = draw_cards(state, selected, 2)
        nxt = _next_unfinished(state, selected)
        state.history.append(
            {
                **_base_event(state, "initial_draw_two", None),
                "penalized": selected,
                "drawn": got,
                "next_seat": nxt,
            }
        )
        state.current_seat = nxt
        state.phase = TURN
    elif initial.kind == REVERSE:
        state.direction = -1
        starter = (selected - 1) % state.config.players
        # starter cannot be finished at setup; keep as computed
        state.history.append(
            {
                **_base_event(state, "initial_reverse", None),
                "selected": selected,
                "next_seat": starter,
            }
        )
        state.current_seat = starter
        state.phase = TURN
    elif initial.kind == WILD:
        state.current_seat = selected
        state.phase = CHOOSE_INITIAL_COLOR
        state.active_color = None
    else:  # pragma: no cover — WD4 looped out above
        msg = "unreachable initial discard"
        raise RuntimeError(msg)


def new_game(config: GameConfig | None = None, seed: int | None = None) -> GameState:
    """Deal and return a fresh game state for the given config and seed."""
    config = config or GameConfig()
    seed = secrets.randbits(256) if seed is None else seed
    rng = random.Random(seed)
    deck = build_canonical_deck()
    rng.shuffle(deck)
    hands: list[list[Card]] = [[] for _ in range(config.players)]
    draw_pile = deck
    for _ in range(config.initial_hand_size):
        for s in range(config.players):
            hands[s].append(draw_pile.pop())

    # initial discard (WD4 reshuffle rule)
    initial = draw_pile.pop()
    reshuffles = 0
    while initial.kind == WILD_DRAW_FOUR:
        draw_pile.append(initial)
        rng.shuffle(draw_pile)
        initial = draw_pile.pop()
        reshuffles += 1
    discard_pile = [initial]

    active_color = None if initial.kind == WILD else initial.color

    selected = rng.randrange(config.players)
    direction = 1
    history: list[JsonDict] = []
    state = GameState(
        config=config,
        seed=seed,
        rng=rng,
        hands=hands,
        draw_pile=draw_pile,
        discard_pile=discard_pile,
        current_seat=selected,
        direction=direction,
        active_color=active_color,
        phase=TURN,
        history=history,
        initial_seed_setup={
            "selected": selected,
            "initial": initial.to_public_dict(),
            "reshuffles": reshuffles,
        },
    )
    history.append(
        {
            "type": "game_start",
            "turn_number": 0,
            "seat": None,
            "hand_sizes": _hand_sizes(state),
            "draw_pile_size": len(draw_pile),
            "selected_starter": selected,
            "initial_discard": initial.to_public_dict(),
        }
    )
    history.append(
        {
            "type": "initial_discard",
            "turn_number": 0,
            "seat": None,
            "card": initial.to_public_dict(),
            "hand_sizes": _hand_sizes(state),
        }
    )

    _apply_initial_effects(state, initial, selected)
    return state


def copy_state(state: GameState) -> GameState:
    """Return a deep copy of a game state."""
    return copy.deepcopy(state)


def state_snapshot_key(state: GameState) -> JsonDict:
    """Minimal deterministic snapshot for reproducibility tests."""
    return {
        "hands": sorted(sorted(c.uid for c in h) for h in state.hands),
        "hands_by_seat": [sorted(c.uid for c in h) for h in state.hands],
        "draw": [c.uid for c in state.draw_pile],
        "discard": [c.uid for c in state.discard_pile],
        "current": state.current_seat,
        "dir": state.direction,
        "color": state.active_color,
        "phase": state.phase,
        "pending_penalty": state.pending_penalty,
        "turns": state.turn_count,
        "finished": list(state.finished_order),
    }


# ---------------------------------------------------------------------------
# legal actions + observation
# ---------------------------------------------------------------------------


def _turn_actions(state: GameState) -> list[EngineAction]:
    seat = state.current_seat
    if seat in state.finished_set:  # defensive; advancement should skip
        return []
    if state.pending_penalty > 0:
        stackable = _stackable_cards(state, seat)
        if stackable:
            # Accepting a penalty remains legal even when a stack is
            # available; the first action stays the deterministic play
            # fallback and ``draw`` is the opt-out.
            return [*expand_play_actions(stackable, state.hands[seat]), EngineAction(type="draw")]
        return [EngineAction(type="draw")]
    playable = _playable_cards(state, seat)
    if playable:
        return expand_play_actions(playable, state.hands[seat])
    return [EngineAction(type="draw")]


def _after_draw_actions(state: GameState) -> list[EngineAction]:
    seat = state.current_seat
    hand = state.hands[seat]
    drawn = _find_card(hand, state.drawn_card_uid) if state.drawn_card_uid is not None else None
    if drawn is None:
        return [EngineAction(type="keep")]
    top = state.discard_pile[-1]
    if not is_card_playable(drawn, top, state.active_color, hand):
        return [EngineAction(type="keep")]
    actions: list[EngineAction] = [EngineAction(type="keep")]
    if drawn.kind in (WILD, WILD_DRAW_FOUR):
        actions.extend(
            EngineAction(type="play", card_uid=drawn.uid, color=col)
            for col in most_common_color_first(hand)
        )
    else:
        actions.append(EngineAction(type="play", card_uid=drawn.uid, color=None))
    return actions


def legal_actions(state: GameState) -> list[EngineAction]:
    """Return the legal engine actions for the current phase and seat."""
    if state.phase == CHOOSE_INITIAL_COLOR:
        return [
            EngineAction(type="choose_color", color=c)
            for c in most_common_color_first(state.hands[state.current_seat])
        ]
    if state.phase == TURN:
        return _turn_actions(state)
    if state.phase == AFTER_DRAW:
        return _after_draw_actions(state)
    return []


def _card_view(card: Card, ref: str) -> JsonDict:
    return {"card_ref": ref, "color": card.color, "kind": card.kind, "value": card.value}


def observe(state: GameState, seat: int) -> JsonDict:
    """Per-seat filtered observation (the per-move broadcast to that seat).

    Contains: own current hand (local refs), played-card context (top discard),
    hand sizes (counts), pile sizes, direction/colour/turn, public history,
    drawn ref in AFTER_DRAW, and explicit legal actions with request-scoped
    ids a0.. in canonical order (a0 == fallback).
    """
    if not 0 <= seat < state.config.players:
        msg = "seat must be 0..3"
        raise ValueError(msg)
    hand = state.hands[seat]
    ordered_hand = order_play_cards(list(hand))
    ref_by_uid: dict[int, str] = {}
    hand_view: list[JsonDict] = []
    for i, c in enumerate(ordered_hand):
        ref = f"h{i}"
        ref_by_uid[c.uid] = ref
        hand_view.append(_card_view(c, ref))

    legal = legal_actions(state) if seat == state.current_seat else []
    # Note: legal_actions() returns [] in FINISHED, so final observations
    # carry no legal actions for any seat by construction.
    legal_view: list[JsonDict] = []
    for i, a in enumerate(legal):
        entry: JsonDict = {"action_id": f"a{i}", "type": a.type}
        if a.card_uid is not None:
            entry["card_ref"] = ref_by_uid.get(a.card_uid)
            card = _find_card(hand, a.card_uid)
            if card is not None:
                entry["card"] = card.to_public_dict()
        if a.color is not None:
            entry["color"] = a.color
        legal_view.append(entry)

    top = state.discard_pile[-1]
    drawn_ref = None
    if (
        state.phase == AFTER_DRAW
        and seat == state.current_seat
        and state.drawn_card_uid is not None
    ):
        drawn_ref = ref_by_uid.get(state.drawn_card_uid)

    return {
        "seat": seat,
        "phase": state.phase,
        "turn_number": state.turn_count + 1,
        "turns_completed": state.turn_count,
        "direction": state.direction,
        "active_color": state.active_color,
        "hand": hand_view,
        "hand_sizes": _hand_sizes(state),
        "top_discard": top.to_public_dict(),
        "draw_pile_size": len(state.draw_pile),
        "discard_pile_size": len(state.discard_pile),
        "history": list(state.history),
        "private_events": [],
        "drawn_card_ref": drawn_ref,
        "legal_actions": legal_view,
        "finished_order": list(state.finished_order),
        "is_finished": state.phase == FINISHED,
    }


def engine_action_from_id(state: GameState, seat: int, action_id: str) -> EngineAction | None:
    """Translate a request-scoped action id (a0..) into an engine action."""
    if seat != state.current_seat:
        return None
    legal = legal_actions(state)
    try:
        idx = int(action_id[1:]) if action_id.startswith("a") else -1
    except ValueError:
        return None
    if 0 <= idx < len(legal):
        return legal[idx]
    return None


# ---------------------------------------------------------------------------
# step
# ---------------------------------------------------------------------------


def _validate_color_choice(state: GameState, action: EngineAction) -> str | None:
    if state.phase != CHOOSE_INITIAL_COLOR:
        return "choose_color only in CHOOSE_INITIAL_COLOR"
    if action.color not in COLORS:
        return "invalid colour choice"
    return None


def _validate_wild_color(card: Card, action: EngineAction) -> str | None:
    if card.kind in (WILD, WILD_DRAW_FOUR):
        if action.color not in COLORS:
            return "wild requires colour choice"
    elif action.color is not None:
        return "non-wild must not set colour"
    return None


def _validate_turn_play(state: GameState, action: EngineAction, seat: int) -> str | None:
    if action.card_uid is None:
        return "play requires card_uid"
    card = _find_card(state.hands[seat], action.card_uid)
    if card is None:
        return "card not in hand"
    top = state.discard_pile[-1]
    if not is_card_playable(card, top, state.active_color, state.hands[seat]):
        return "card not legally playable"
    return _validate_wild_color(card, action)


def _validate_stack_play(state: GameState, action: EngineAction, seat: int) -> str | None:
    """Validate a play made while answering an unresolved penalty."""
    if action.card_uid is None:
        return "play requires card_uid"
    card = _find_card(state.hands[seat], action.card_uid)
    if card is None:
        return "card not in hand"
    top = state.discard_pile[-1]
    if not is_card_stackable(card, top, state.pending_penalty):
        return "card cannot be stacked on the pending penalty"
    return _validate_wild_color(card, action)


def _validate_turn(state: GameState, action: EngineAction) -> str | None:
    seat = state.current_seat
    if state.pending_penalty > 0:
        if action.type == "draw":
            return None if action.card_uid is None else "penalty draw must not set card_uid"
        if action.type != "play":
            return "must stack a multiplier or draw the pending penalty"
        return _validate_stack_play(state, action, seat)
    playable = _playable_cards(state, seat)
    if not playable:
        if action.type != "draw" or action.card_uid is not None:
            return "must draw when no play is available"
        return None
    if action.type != "play":
        return "must play when a play is available (restricted drawing)"
    return _validate_turn_play(state, action, seat)


def _validate_after_draw(state: GameState, action: EngineAction) -> str | None:
    if action.type == "keep":
        return None
    if action.type != "play":
        return "AFTER_DRAW expects play-drawn or keep"
    uid = action.card_uid
    if uid != state.drawn_card_uid:
        return "only the drawn card may be played after a draw"
    seat = state.current_seat
    card = _find_card(state.hands[seat], uid) if uid is not None else None
    if card is None:
        return "drawn card not in hand"
    top = state.discard_pile[-1]
    if not is_card_playable(card, top, state.active_color, state.hands[seat]):
        return "drawn card not playable"
    return _validate_wild_color(card, action)


def _validate(state: GameState, action: EngineAction) -> str | None:
    """Return error string or None if valid. No mutation here."""
    if state.phase == FINISHED:
        return "game already finished"
    if action.type == "choose_color":
        return _validate_color_choice(state, action)
    if state.phase == CHOOSE_INITIAL_COLOR:
        return "expected choose_color"
    if state.phase == TURN:
        return _validate_turn(state, action)
    if state.phase == AFTER_DRAW:
        return _validate_after_draw(state, action)
    return "unknown phase"


def _finish_if_empty(state: GameState, seat: int, events: list[JsonDict]) -> None:
    if len(state.hands[seat]) == 0 and seat not in state.finished_set:
        state.finished_set.add(seat)
        state.finished_order.append(seat)
        events.append(
            {**_base_event(state, "finish", seat), "placement": len(state.finished_order)}
        )


def _maybe_end_game(state: GameState, events: list[JsonDict]) -> bool:
    if len(state.finished_order) >= state.config.players - 1:
        remaining = _remaining_unfinished(state)
        if remaining:
            # auto-place the last seat
            last = remaining[0]
            if len(state.finished_order) == state.config.players - 1:
                state.finished_set.add(last)
                state.finished_order.append(last)
                events.append({**_base_event(state, "finish_auto", last), "placement": state.config.players})
        state.outcome = Outcome(
            placements=list(state.finished_order), is_draw=False, unfinished=[], reason="all_placed"
        )
        state.phase = FINISHED
        events.append(
            {
                **_base_event(state, "game_end", None),
                "placements": list(state.finished_order),
                "is_draw": False,
            }
        )
        return True
    return False


def _check_turn_limit(state: GameState, events: list[JsonDict]) -> bool:
    if state.turn_count >= state.config.max_turns and state.phase != FINISHED:
        finished = list(state.finished_order)
        unfinished = _remaining_unfinished(state)
        if not finished:
            state.outcome = Outcome(
                placements=[], is_draw=True, unfinished=unfinished, reason="turn_limit_empty"
            )
        else:
            state.outcome = Outcome(
                placements=finished,
                is_draw=True,
                unfinished=unfinished,
                reason="turn_limit_partial",
            )
        state.phase = FINISHED
        events.append(
            {
                **_base_event(state, "game_end", None),
                "placements": finished,
                "is_draw": True,
                "unfinished": unfinished,
                "reason": state.outcome.reason,
            }
        )
        return True
    return False


def _step_choose_color(state: GameState, action: EngineAction) -> Transition:
    if action.color not in COLORS:
        msg = "validated colour choice missing"
        raise AssertionError(msg)
    events: list[JsonDict] = []
    state.active_color = action.color
    state.phase = TURN
    events.append(
        {**_base_event(state, "initial_color_choice", state.current_seat), "color": action.color}
    )
    _push_history_bulk(state, events)
    return Transition(events=events, outcome=None)


def _step_draw(state: GameState) -> Transition:
    if state.pending_penalty > 0:
        return _step_penalty_draw(state)
    events: list[JsonDict] = []
    seat = state.current_seat
    got = draw_cards(state, seat, 1)
    if got == 0:
        events.append({**_base_event(state, "draw", seat), "drawn": 0})
        state.turn_count += 1
        if not _check_turn_limit(state, events):
            state.current_seat = _next_unfinished(state, seat)
        _push_history_bulk(state, events)
        return Transition(events=events, outcome=state.outcome)
    drawn_card = state.hands[seat][-1]
    events.append({**_base_event(state, "draw", seat), "drawn": 1})
    top = state.discard_pile[-1]
    if is_card_playable(drawn_card, top, state.active_color, state.hands[seat]):
        state.phase = AFTER_DRAW
        state.drawn_card_uid = drawn_card.uid
        # turn not yet completed; no advancement
    else:
        events.append({**_base_event(state, "keep_auto", seat)})
        state.turn_count += 1
        if not _check_turn_limit(state, events):
            state.current_seat = _next_unfinished(state, seat)
    _push_history_bulk(state, events)
    return Transition(events=events, outcome=state.outcome)


def _step_penalty_draw(state: GameState) -> Transition:
    """Resolve the current accumulated penalty and skip that seat's turn."""
    events: list[JsonDict] = []
    seat = state.current_seat
    penalty = state.pending_penalty
    source = state.pending_source
    top_kind = state.discard_pile[-1].kind
    got = draw_cards(state, seat, penalty)
    events.append(
        {
            **_base_event(state, "penalty_draw", seat),
            "count": got,
            "penalty": penalty,
            "cause": top_kind,
            "from": source,
        }
    )
    state.pending_penalty = 0
    state.pending_source = None
    state.turn_count += 1
    if not _check_turn_limit(state, events):
        state.current_seat = _next_unfinished(state, seat)
    _push_history_bulk(state, events)
    return Transition(events=events, outcome=state.outcome)


def _resolve_card_effects(
    state: GameState, card: Card, seat: int, events: list[JsonDict]
) -> tuple[int | None, int | None]:
    def queue_or_resolve(penalty: int, recipient: int) -> None:
        """Offer a final-card stack when possible, otherwise draw now."""
        state.pending_penalty = penalty
        state.pending_source = seat
        # A finishing attack keeps the historical penalty-before-finish
        # ordering when the recipient has no possible response. If a stack is
        # available, leave the chain open so the recipient can use it.
        if state.hands[seat] or _stackable_cards(state, recipient):
            return
        got = draw_cards(state, recipient, penalty)
        events.append(
            {
                **_base_event(state, "penalty_draw", recipient),
                "count": got,
                "penalty": penalty,
                "cause": card.kind,
                "from": seat,
            }
        )
        state.pending_penalty = 0
        state.pending_source = None

    if card.kind == DRAW_TWO:
        recipient = _next_unfinished(state, seat)
        penalty = state.pending_penalty + 2
        queue_or_resolve(penalty, recipient)
        return recipient, None
    if card.kind == WILD_DRAW_FOUR:
        recipient = _next_unfinished(state, seat)
        penalty = state.pending_penalty + 4
        queue_or_resolve(penalty, recipient)
        return recipient, None
    if card.kind == SKIP:
        skip_target = _next_unfinished(state, seat)
        events.append({**_base_event(state, "skip", seat), "skipped": skip_target})
        return None, skip_target
    if card.kind == REVERSE:
        state.direction *= -1
        events.append({**_base_event(state, "reverse", seat), "direction": state.direction})
    return None, None


def _advance_after_play(
    state: GameState, card: Card, seat: int, recipient: int | None, skip_target: int | None
) -> None:
    if card.kind in (DRAW_TWO, WILD_DRAW_FOUR) and recipient is not None:
        if state.pending_penalty > 0:
            # The recipient must decide whether to continue the chain or
            # accept the accumulated penalty.
            state.current_seat = recipient
        else:
            # A finishing attack is resolved immediately so the historical
            # final-card penalty rule remains intact.
            state.current_seat = _next_unfinished(state, recipient)
    elif card.kind == SKIP:
        if skip_target is None:
            msg = "skip target missing after validation"
            raise AssertionError(msg)
        state.current_seat = _next_unfinished(state, skip_target)
    else:
        state.current_seat = _next_unfinished(state, seat)


def _step_play(state: GameState, action: EngineAction) -> Transition:
    events: list[JsonDict] = []
    seat = state.current_seat
    card = _find_card(state.hands[seat], action.card_uid) if action.card_uid is not None else None
    if card is None:
        msg = "validated play card missing from hand"
        raise AssertionError(msg)
    # remove + discard
    state.hands[seat] = [c for c in state.hands[seat] if c.uid != card.uid]
    state.discard_pile.append(card)
    new_color = action.color if card.kind in (WILD, WILD_DRAW_FOUR) else card.color
    state.active_color = new_color
    play_ev: JsonDict = {
        **_base_event(state, "play", seat),
        "card": card.to_public_dict(),
        "active_color": new_color,
    }
    if state.phase == AFTER_DRAW:
        play_ev["from_draw"] = True
    events.append(play_ev)
    if state.phase == AFTER_DRAW:
        state.phase = TURN
        state.drawn_card_uid = None

    # resolve card effects + penalties before finish recording
    recipient, skip_target = _resolve_card_effects(state, card, seat, events)
    if card.kind in (DRAW_TWO, WILD_DRAW_FOUR):
        # The amount is public through the play event without adding a new
        # protocol action or leaking any hidden card identity.
        play_ev["pending_penalty"] = state.pending_penalty
    _finish_if_empty(state, seat, events)
    state.turn_count += 1
    if _maybe_end_game(state, events):
        _push_history_bulk(state, events)
        return Transition(events=events, outcome=state.outcome)
    if _check_turn_limit(state, events):
        _push_history_bulk(state, events)
        return Transition(events=events, outcome=state.outcome)
    _advance_after_play(state, card, seat, recipient, skip_target)
    _push_history_bulk(state, events)
    return Transition(events=events, outcome=state.outcome)


def _step_keep(state: GameState) -> Transition:
    events: list[JsonDict] = []
    seat = state.current_seat
    events.append({**_base_event(state, "keep", seat)})
    state.phase = TURN
    state.drawn_card_uid = None
    state.turn_count += 1
    if not _check_turn_limit(state, events):
        state.current_seat = _next_unfinished(state, seat)
    _push_history_bulk(state, events)
    return Transition(events=events, outcome=state.outcome)


def step(state: GameState, action: EngineAction) -> Transition:
    """Apply one validated decision. Invalid actions: error, no mutation."""
    err = _validate(state, action)
    if err is not None:
        return Transition(events=[], outcome=state.outcome, error=err)
    if state.phase == CHOOSE_INITIAL_COLOR and action.type == "choose_color":
        return _step_choose_color(state, action)
    if state.phase == TURN and action.type == "draw":
        return _step_draw(state)
    if action.type == "play":
        return _step_play(state, action)
    if state.phase == AFTER_DRAW and action.type == "keep":
        return _step_keep(state)
    return Transition(events=[], outcome=state.outcome, error="unhandled action")


def _push_history_bulk(state: GameState, events: list[JsonDict]) -> None:
    # refresh hand_sizes snapshots to post-step values for broadcast accuracy
    sizes = _hand_sizes(state)
    for e in events:
        e["hand_sizes"] = list(sizes)
        if e["type"] in (
            "play",
            "draw",
            "keep",
            "keep_auto",
            "penalty_draw",
            "skip",
            "reverse",
            "finish",
            "finish_auto",
            "game_end",
            "initial_color_choice",
        ):
            e.setdefault("draw_pile_size", len(state.draw_pile))
            e.setdefault("discard_pile_size", len(state.discard_pile))
        state.history.append(e)


def total_cards(state: GameState) -> int:
    """Return the total number of cards across hands and both piles."""
    return sum(len(h) for h in state.hands) + len(state.draw_pile) + len(state.discard_pile)
