"""Persistent NDJSON SDK for Python 3.12 agents."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

MAX_LINE_BYTES = 65_536
MAX_RESPONSE_BYTES = 16_384
def _protocol_fd_env_name() -> str:
    """Resolve the descriptor variable from the host environment.

    The host may advertise the protocol descriptor under a game-specific
    ``*_PROTOCOL_FD`` name. An explicit override is available for testing.
    """
    override = os.environ.get("ARENA_PROTOCOL_FD_ENV")
    if override:
        return override
    for name in os.environ:
        if name.endswith("_PROTOCOL_FD"):
            return name
    return "PROTOCOL_FD"


PROTOCOL_FD_ENV = _protocol_fd_env_name()


class UnitKind(str, Enum):
    STRIDER = "STRIDER"
    BULWARK = "BULWARK"
    HUNTER = "HUNTER"


class TerrainKind(str, Enum):
    WALL = "WALL"
    FLUX_WELL = "FLUX_WELL"
    CONTROL_RELAY = "CONTROL_RELAY"


class TelegraphKind(str, Enum):
    PULSE = "PULSE"
    SEAL = "SEAL"
    CORRUPT = "CORRUPT"
    HUNT = "HUNT"


class Direction(str, Enum):
    N = "N"
    E = "E"
    S = "S"
    W = "W"


class ActionKind(str, Enum):
    MOVE = "MOVE"
    ATTACK = "ATTACK"
    DEFEND = "DEFEND"
    HARVEST = "HARVEST"
    CAPTURE = "CAPTURE"
    SCAN = "SCAN"
    WAIT = "WAIT"


def _object(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"invalid {name} object")
    return value


def _int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"invalid {name}")
    return value


def _enum(enum_type: type[Enum], value: Any, name: str) -> Any:
    if not isinstance(value, str):
        raise ValueError(f"invalid {name}")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"invalid {name}") from error


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"invalid {name}")
    return value


def _read_bounded_line(stream: Any) -> tuple[bytes, bool] | None:
    line = bytearray()
    oversized = False
    while True:
        read_limit = 4096 if oversized else MAX_LINE_BYTES + 1 - len(line)
        chunk = stream.readline(read_limit)
        if not chunk:
            if not line and not oversized:
                return None
            return bytes(line), oversized
        if not oversized:
            if len(line) + len(chunk) > MAX_LINE_BYTES:
                line.clear()
                oversized = True
            else:
                line.extend(chunk)
        if chunk.endswith(b"\n"):
            return bytes(line), oversized


@dataclass(frozen=True)
class Coord:
    x: int
    y: int

    @classmethod
    def from_wire(cls, value: Any) -> Coord:
        item = _object(value, "coordinate", {"x", "y"})
        return cls(_int(item["x"], "x", 0, 255), _int(item["y"], "y", 0, 255))


@dataclass(frozen=True)
class UnitObservation:
    id: int
    team_id: int | None
    kind: UnitKind
    x: int
    y: int
    hp: int
    immune: bool

    @classmethod
    def from_wire(cls, value: Any) -> UnitObservation:
        item = _object(value, "unit", {"id", "team_id", "kind", "x", "y", "hp", "immune"})
        team_id = item["team_id"]
        return cls(
            _int(item["id"], "unit id", 0, 65_535),
            None if team_id is None else _int(team_id, "team id", 0, 255),
            _enum(UnitKind, item["kind"], "unit kind"),
            _int(item["x"], "unit x", 0, 255),
            _int(item["y"], "unit y", 0, 255),
            _int(item["hp"], "unit hp", -32_768, 32_767),
            _bool(item["immune"], "unit immunity"),
        )


@dataclass(frozen=True)
class TeamObservation:
    id: int
    flux: int
    units: tuple[UnitObservation, ...]

    @classmethod
    def from_wire(cls, value: Any) -> TeamObservation:
        item = _object(value, "team", {"id", "flux", "units"})
        units = _array(item["units"], "team units")
        return cls(
            _int(item["id"], "team id", 0, 255),
            _int(item["flux"], "team flux", 0, 255),
            tuple(UnitObservation.from_wire(unit) for unit in units),
        )


@dataclass(frozen=True)
class TerrainObservation:
    x: int
    y: int
    kind: TerrainKind
    object_id: int | None

    @classmethod
    def from_wire(cls, value: Any) -> TerrainObservation:
        item = _object(value, "terrain", {"x", "y", "kind", "object_id"})
        object_id = item["object_id"]
        return cls(
            _int(item["x"], "terrain x", 0, 255),
            _int(item["y"], "terrain y", 0, 255),
            _enum(TerrainKind, item["kind"], "terrain kind"),
            None if object_id is None else _int(object_id, "terrain object id", 0, 255),
        )


@dataclass(frozen=True)
class WellObservation:
    id: int
    position: Coord
    charged: bool
    available_from_tick: int | None

    @classmethod
    def from_wire(cls, value: Any) -> WellObservation:
        item = _object(value, "well", {"id", "position", "charged", "available_from_tick"})
        available = item["available_from_tick"]
        return cls(
            _int(item["id"], "well id", 0, 255),
            Coord.from_wire(item["position"]),
            _bool(item["charged"], "well charge"),
            None if available is None else _int(available, "well availability tick", 0, 65_535),
        )


@dataclass(frozen=True)
class RelayObservation:
    id: int
    x: int
    y: int
    owner_team_id: int | None
    contested: bool
    corrupted: bool
    uncontested_owner_ticks: int

    @classmethod
    def from_wire(cls, value: Any) -> RelayObservation:
        item = _object(value, "relay", {"id", "x", "y", "owner_team_id", "contested", "corrupted", "uncontested_owner_ticks"})
        owner = item["owner_team_id"]
        return cls(
            _int(item["id"], "relay id", 0, 255),
            _int(item["x"], "relay x", 0, 255),
            _int(item["y"], "relay y", 0, 255),
            None if owner is None else _int(owner, "relay owner", 0, 255),
            _bool(item["contested"], "relay contested"),
            _bool(item["corrupted"], "relay corrupted"),
            _int(item["uncontested_owner_ticks"], "relay owner ticks", 0, 255),
        )


@dataclass(frozen=True)
class WardenTelegraph:
    kind: TelegraphKind
    starts_at_tick: int
    ends_after_tick: int
    center: Coord | None
    relay_id: int | None
    tiles: tuple[Coord, ...]

    @classmethod
    def from_wire(cls, value: Any) -> WardenTelegraph:
        item = _object(value, "Warden telegraph", {"kind", "starts_at_tick", "ends_after_tick", "center", "relay_id", "tiles"})
        center = item["center"]
        relay_id = item["relay_id"]
        return cls(
            _enum(TelegraphKind, item["kind"], "telegraph kind"),
            _int(item["starts_at_tick"], "telegraph start", 0, 65_535),
            _int(item["ends_after_tick"], "telegraph end", 0, 65_535),
            None if center is None else Coord.from_wire(center),
            None if relay_id is None else _int(relay_id, "telegraph relay id", 0, 255),
            tuple(Coord.from_wire(tile) for tile in _array(item["tiles"], "telegraph tiles")),
        )


@dataclass(frozen=True)
class ContractionStep:
    warning_at_tick: int
    starts_at_tick: int
    safe_radius: int

    @classmethod
    def from_wire(cls, value: Any) -> ContractionStep:
        item = _object(value, "contraction", {"warning_at_tick", "starts_at_tick", "safe_radius"})
        return cls(
            _int(item["warning_at_tick"], "warning tick", 0, 65_535),
            _int(item["starts_at_tick"], "contraction tick", 0, 65_535),
            _int(item["safe_radius"], "safe radius", 0, 255),
        )


@dataclass(frozen=True)
class PhaseFourState:
    safe_zone_center_tiles: tuple[Coord, ...]
    safe_radius: int
    hazard_damage: int
    next_contraction: ContractionStep | None

    @classmethod
    def from_wire(cls, value: Any) -> PhaseFourState:
        item = _object(value, "phase-four state", {"safe_zone_center_tiles", "safe_radius", "hazard_damage", "next_contraction"})
        centers = tuple(Coord.from_wire(tile) for tile in _array(item["safe_zone_center_tiles"], "safe-zone centers"))
        if len(centers) != 4:
            raise ValueError("invalid safe-zone centers")
        next_contraction = item["next_contraction"]
        return cls(
            centers,
            _int(item["safe_radius"], "safe radius", 0, 255),
            _int(item["hazard_damage"], "hazard damage", -32_768, 32_767),
            None if next_contraction is None else ContractionStep.from_wire(next_contraction),
        )


@dataclass(frozen=True)
class Observation:
    match_id: str
    tick: int
    team: TeamObservation
    visible_enemies: tuple[UnitObservation, ...]
    terrain: tuple[TerrainObservation, ...]
    wells: tuple[WellObservation, ...]
    relays: tuple[RelayObservation, ...]
    warden_telegraphs: tuple[WardenTelegraph, ...]
    phase_four: PhaseFourState | None

    @classmethod
    def from_wire(cls, value: Any) -> Observation:
        item = _object(value, "observation", {"match_id", "tick", "team", "visible_enemies", "terrain", "wells", "relays", "warden_telegraphs", "phase_four"})
        if not isinstance(item["match_id"], str):
            raise ValueError("invalid match id")
        phase_four = item["phase_four"]
        return cls(
            item["match_id"],
            _int(item["tick"], "tick", 0, 65_535),
            TeamObservation.from_wire(item["team"]),
            tuple(UnitObservation.from_wire(unit) for unit in _array(item["visible_enemies"], "visible enemies")),
            tuple(TerrainObservation.from_wire(cell) for cell in _array(item["terrain"], "terrain")),
            tuple(WellObservation.from_wire(well) for well in _array(item["wells"], "wells")),
            tuple(RelayObservation.from_wire(relay) for relay in _array(item["relays"], "relays")),
            tuple(WardenTelegraph.from_wire(event) for event in _array(item["warden_telegraphs"], "Warden telegraphs")),
            None if phase_four is None else PhaseFourState.from_wire(phase_four),
        )


@dataclass(frozen=True)
class UnitAction:
    kind: ActionKind
    unit_id: int
    direction: Direction | None = None
    steps: int | None = None
    target_unit_id: int | None = None
    relay_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            raise ValueError("invalid action kind")
        _int(self.unit_id, "action unit id", 0, 65_535)
        if self.kind is ActionKind.MOVE:
            if not isinstance(self.direction, Direction) or self.steps is None:
                raise ValueError("MOVE requires direction and steps")
            _int(self.steps, "move steps", 1, 2)
            if self.target_unit_id is not None or self.relay_id is not None:
                raise ValueError("MOVE has unexpected parameters")
        elif self.kind is ActionKind.ATTACK:
            if self.target_unit_id is None:
                raise ValueError("ATTACK requires a target unit")
            _int(self.target_unit_id, "target unit id", 0, 65_535)
            if self.direction is not None or self.steps is not None or self.relay_id is not None:
                raise ValueError("ATTACK has unexpected parameters")
        elif self.kind is ActionKind.CAPTURE:
            if self.relay_id is None:
                raise ValueError("CAPTURE requires a relay")
            _int(self.relay_id, "relay id", 0, 255)
            if self.direction is not None or self.steps is not None or self.target_unit_id is not None:
                raise ValueError("CAPTURE has unexpected parameters")
        elif any(value is not None for value in (self.direction, self.steps, self.target_unit_id, self.relay_id)):
            raise ValueError(f"{self.kind.value} has unexpected parameters")

    @classmethod
    def move(cls, unit_id: int, direction: Direction, steps: int) -> UnitAction:
        return cls(ActionKind.MOVE, unit_id, direction=direction, steps=steps)

    @classmethod
    def attack(cls, unit_id: int, target_unit_id: int) -> UnitAction:
        return cls(ActionKind.ATTACK, unit_id, target_unit_id=target_unit_id)

    @classmethod
    def defend(cls, unit_id: int) -> UnitAction:
        return cls(ActionKind.DEFEND, unit_id)

    @classmethod
    def harvest(cls, unit_id: int) -> UnitAction:
        return cls(ActionKind.HARVEST, unit_id)

    @classmethod
    def capture(cls, unit_id: int, relay_id: int) -> UnitAction:
        return cls(ActionKind.CAPTURE, unit_id, relay_id=relay_id)

    @classmethod
    def scan(cls, unit_id: int) -> UnitAction:
        return cls(ActionKind.SCAN, unit_id)

    @classmethod
    def wait(cls, unit_id: int) -> UnitAction:
        return cls(ActionKind.WAIT, unit_id)

    def to_wire(self) -> dict[str, Any]:
        value: dict[str, Any] = {"unit_id": self.unit_id, "action": self.kind.value}
        if self.kind is ActionKind.MOVE:
            value.update(direction=self.direction.value, steps=self.steps)
        elif self.kind is ActionKind.ATTACK:
            value["target_unit_id"] = self.target_unit_id
        elif self.kind is ActionKind.CAPTURE:
            value["relay_id"] = self.relay_id
        return value


@dataclass(frozen=True)
class ActionBundle:
    actions: tuple[UnitAction, ...] = ()
    priority: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "actions", tuple(self.actions))
            object.__setattr__(self, "priority", tuple(self.priority))
        except TypeError as error:
            raise ValueError("actions and priority must be sequences") from error
        if len(self.actions) > 2 or any(not isinstance(action, UnitAction) for action in self.actions):
            raise ValueError("action bundle must contain at most two typed actions")
        if len(self.priority) > 2:
            raise ValueError("priority must contain at most two unit ids")
        for unit_id in self.priority:
            _int(unit_id, "priority unit id", 0, 65_535)

    def to_wire(self) -> dict[str, Any]:
        return {
            "actions": [action.to_wire() for action in self.actions],
            "priority": list(self.priority),
        }


def _protocol_writer_fd() -> int:
    configured_fd = os.environ.pop(PROTOCOL_FD_ENV, None)
    if configured_fd is None:
        protocol_fd = os.dup(1)
    else:
        protocol_fd = int(configured_fd)
        if protocol_fd < 3:
            raise ValueError("invalid protocol writer descriptor")
        os.fstat(protocol_fd)
    os.dup2(2, 1)
    return protocol_fd


def _write_protocol(fd: int, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("protocol response exceeds the size limit")
    view = memoryview(encoded)
    while view:
        written = os.write(fd, view)
        view = view[written:]


_U64_MASK = (1 << 64) - 1
_U64_RANGE = 1 << 64
_AGENT_RNG_DOMAIN = b"relay-arena-agent-rng-v1\0"


class AgentRng:
    """Persistent SplitMix64 stream seeded from public match/team identity."""

    def __init__(self, match_id: str, team_id: int) -> None:
        if not isinstance(match_id, str) or not match_id:
            raise ValueError("invalid agent RNG match ID")
        if type(team_id) is not int or not 0 <= team_id <= 255:
            raise ValueError("invalid agent RNG team ID")
        state = 0xCBF29CE484222325
        for byte in _AGENT_RNG_DOMAIN + match_id.encode("utf-8") + b"\xff" + bytes((team_id,)):
            state = ((state ^ byte) * 0x100000001B3) & _U64_MASK
        self._state = state

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _U64_MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _U64_MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _U64_MASK
        return value ^ (value >> 31)

    def below(self, upper_exclusive: int) -> int:
        if type(upper_exclusive) is not int or not 1 <= upper_exclusive <= _U64_MASK:
            raise ValueError("upper_exclusive must be an integer in [1, 2^64-1]")
        threshold = _U64_RANGE % upper_exclusive
        while True:
            value = self.next_u64()
            if value >= threshold:
                return value % upper_exclusive


_agent_rng: AgentRng | None = None
_agent_rng_identity: tuple[str, int] | None = None


def agent_rng() -> AgentRng:
    """Return the process's match-scoped PRNG inside ``decide(state)``."""
    if _agent_rng is None:
        raise RuntimeError("agent_rng() is available after the first scored observation")
    return _agent_rng


def _initialize_agent_rng(observation: Observation) -> None:
    global _agent_rng, _agent_rng_identity
    identity = (observation.match_id, observation.team.id)
    if _agent_rng is None:
        _agent_rng = AgentRng(*identity)
        _agent_rng_identity = identity
    elif identity != _agent_rng_identity:
        raise ValueError("SDK process received a different match/team identity")


def run(decision_fn: Callable[[Observation], ActionBundle]) -> None:
    protocol_fd = _protocol_writer_fd()
    while True:
        frame = _read_bounded_line(sys.stdin.buffer)
        if frame is None:
            break
        raw, oversized = frame
        if oversized:
            print("observation exceeds protocol size limit", file=sys.stderr)
            continue
        try:
            request = json.loads(raw)
            match_id = request["match_id"]
            tick = request["tick"]
            if (
                not isinstance(match_id, str)
                or type(tick) is not int
                or not 0 <= tick <= 65_535
            ):
                raise ValueError("invalid match_id/tick")
            if tick == 0:
                _write_protocol(protocol_fd, {"match_id": match_id, "tick": 0, "status": "READY", "actions": [], "priority": []})
                continue
            observation = Observation.from_wire(request.get("observation"))
            if observation.match_id != match_id or observation.tick != tick:
                raise ValueError("outer and observation identity or tick differ")
            _initialize_agent_rng(observation)
            result = decision_fn(observation)
            if not isinstance(result, ActionBundle):
                raise ValueError("decide(state) must return an ActionBundle")
            _write_protocol(protocol_fd, {"match_id": match_id, "tick": tick, **result.to_wire()})
        except Exception as error:  # The host applies its published failure policy.
            print(f"agent protocol error: {type(error).__name__}", file=sys.stderr)


if __name__ == "__main__":
    run()
