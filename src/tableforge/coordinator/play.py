"""Remote play: games against the cluster's models, run on the cluster host.

Callers with `play:remote` start a game here; the models are loaded from the cluster's
model library and run on this machine (no worker job is involved, so the person's
decisions are answered at once). The loaded networks are shared through a small cache;
each seat still gets its own explainer because agents keep per-hand state.

`ModelSeats` is the only place that knows where models come from; a dedicated inference
node later only needs another `SeatFactory`.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tableforge.learning.explain import DecisionExplainer
from tableforge.learning.play import (
    GameStore,
    PlayError,
    PlayService,
    SeatChoice,
    parse_seats,
)
from tableforge.models import ModelInfo, ModelLibrary
from tableforge.poker.tournament import TournamentConfig
from tableforge.web.cluster import model_library_root

MAX_CACHED_MODELS = 8


class ModelSeats:
    """Builds model seats from a model library (checkpoints are sha256-checked on read)."""

    def __init__(self, library: Callable[[], ModelLibrary]) -> None:
        self._library = library
        self._bundles: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def info(self, model_id: str) -> ModelInfo:
        try:
            return self._library().info(model_id)
        except KeyError as error:
            raise PlayError(f"no such model: {model_id}") from error

    def __call__(self, choice: SeatChoice, player: int) -> DecisionExplainer:
        del player
        info = self.info(choice.name)
        if info.algorithm != "nfsp":
            raise PlayError(f"model {info.model_id} uses {info.algorithm}, which cannot play yet")
        from tableforge.learning.nfsp_explain import NfspExplainer

        return NfspExplainer(self._bundle(info))

    def _bundle(self, info: ModelInfo) -> bytes:
        key = info.sha256
        with self._lock:
            if key in self._bundles:
                self._bundles.move_to_end(key)
                return self._bundles[key]
        content = self._library().checkpoint(info.model_id)
        with self._lock:
            self._bundles[key] = content
            while len(self._bundles) > MAX_CACHED_MODELS:
                self._bundles.popitem(last=False)
        return content


def game_config(seats: ModelSeats, arguments: dict[str, Any]) -> TournamentConfig:
    """The tournament to play: an explicit spec, or the one the first model was trained on."""
    from tableforge.coordinator.analysis import tournament_json
    from tableforge.poker.tournament_spec import TournamentSpec

    spec = arguments.get("tournament")
    if spec:
        if not isinstance(spec, dict):
            raise PlayError("tournament must be an object")
        try:
            return TournamentSpec.from_dict(spec).to_config()
        except ValueError as error:
            raise PlayError(str(error)) from error
    raw = arguments.get("seats")
    models = [
        str(item.get("name", ""))
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and item.get("kind") == "model"
    ]
    if not models:
        raise PlayError("choose a tournament, or seat at least one model")
    try:
        return TournamentConfig.from_json(tournament_json(seats.info(models[0])))
    except ValueError as error:
        raise PlayError(str(error)) from error


def play_command(
    service: PlayService, seats: ModelSeats, owner: str, action: str, arguments: dict[str, Any]
) -> object:
    """`play_start`, `play_act`, `play_state`, `play_replay`, `play_history`, `play_close`."""
    if action == "play_start":
        config = game_config(seats, arguments)
        choices = parse_seats(arguments.get("seats"), config.players)
        hint = arguments.get("hint_model")
        hint_model = None
        if hint:
            hint_model = SeatChoice("model", str(hint)[:64], str(arguments.get("hint_mode", "")))
        seed = arguments.get("seed")
        return service.start(
            owner,
            config,
            choices,
            int(seed) if seed is not None else _random_seed(),
            hints=bool(arguments.get("hints", False)),
            practice=bool(arguments.get("practice", False)),
            hint_model=hint_model,
        )
    if action == "play_list":
        return service.sessions(owner)
    session_id = str(arguments.get("session_id", ""))
    if action == "play_act":
        amount = arguments.get("amount")
        return service.act(
            owner,
            session_id,
            str(arguments.get("action", "")),
            int(amount) if amount is not None else None,
        )
    if action == "play_state":
        return service.view(owner, session_id)
    if action == "play_replay":
        return service.replay(owner, session_id)
    if action == "play_history":
        return {"text": service.hand_history(owner, session_id)}
    if action == "play_report":
        return service.report(owner, session_id)
    if action == "play_close":
        service.close(owner, session_id)
        return {"ok": True}
    raise PlayError(f"unknown play action: {action}")


PLAY_ACTIONS = (
    "play_start",
    "play_list",
    "play_act",
    "play_state",
    "play_replay",
    "play_history",
    "play_report",
    "play_close",
)


def _random_seed() -> int:
    return secrets.randbelow(2**31)


_HOSTS: dict[str, tuple[PlayService, ModelSeats]] = {}
_HOSTS_LOCK = threading.Lock()


def remote_play(data_dir: Path) -> tuple[PlayService, ModelSeats]:
    """The play service of the cluster stored in `data_dir` (one per process).

    The web API and the gRPC control service share it, so a game started in a browser can
    be continued from `tableforge ui` with the same credential and vice versa.
    """
    key = str(data_dir.resolve())
    with _HOSTS_LOCK:
        if key not in _HOSTS:
            seats = ModelSeats(lambda: ModelLibrary(model_library_root(data_dir)))
            _HOSTS[key] = (PlayService(seats, store=GameStore(data_dir / "games")), seats)
        return _HOSTS[key]
