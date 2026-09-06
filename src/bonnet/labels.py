"""Label store: persistence + import + auto-bootstrap for the labeled dataset.

Three ways to populate labels:
  1. **Manual**: `bonnet label <address> good|moon|rug [--symbol S] [--notes "..."]`
  2. **Import**: `bonnet label-import <file>` (CSV or JSON; see _IMPORT_HELP below)
  3. **Auto-bootstrap**: `bonnet label-auto` — uses scoring heuristics to assign
     labels to existing watchlist/scored tokens, and pulls fresh tokens from
     DexScreener to label by price action

Labels persist to `data/labels.json` and are consumed by `bonnet backtest`.

Why a separate file from labels (storage) module?
  - `storage.sqlite` is for runtime ephemeral state (score history, alerts)
  - `data/labels.json` is for **durable, manually-curated** labels that survive
    across deploys and are checked into git
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC
from pathlib import Path

from .logging import get_logger

log = get_logger("bonnet.labels")

VALID_LABELS = ("good", "moon", "rug")


@dataclass
class Label:
    address: str
    label: str  # one of VALID_LABELS
    symbol: str = ""
    notes: str = ""
    source: str = "manual"  # manual | import | auto
    confidence: float = 1.0  # 1.0 = manual, <1.0 = auto
    labeled_at: str = ""  # ISO timestamp

    def __post_init__(self) -> None:
        self.address = self.address.lower()
        # Validate label set
        if self.label not in VALID_LABELS:
            raise ValueError(
                f"label must be one of {VALID_LABELS}, got {self.label!r}"
            )
        # Validate address shape
        if not self.address.startswith("0x") or len(self.address) != 42:
            raise ValueError(
                f"address must be 0x-prefixed 40-hex, got {self.address!r}"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Label:
        return cls(
            address=d["address"],
            label=d["label"],
            symbol=d.get("symbol", ""),
            notes=d.get("notes", ""),
            source=d.get("source", "manual"),
            confidence=d.get("confidence", 1.0),
            labeled_at=d.get("labeled_at", ""),
        )


@dataclass
class LabelStore:
    """File-backed set of token labels, addressed by token address (lowercased)."""

    path: Path
    labels: dict[str, Label] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> LabelStore:
        store = cls(path=path)
        if path.exists():
            data = json.loads(path.read_text())
            for entry in data.get("labels", []):
                lbl = Label.from_dict(entry)
                store.labels[lbl.address] = lbl
        log.info("labels_loaded", count=len(store.labels), path=str(path))
        return store

    def save(self) -> None:
        """Persist all labels to disk atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(
            {"schema_version": 1, "labels": [lbl.to_dict() for lbl in self.labels.values()]},
            indent=2,
            sort_keys=True,
        ))
        tmp.replace(self.path)
        log.info("labels_saved", count=len(self.labels), path=str(self.path))

    def add(self, label: Label) -> bool:
        """Add or update a label. Returns True if it was a new entry."""
        is_new = label.address not in self.labels
        # Manual labels always overwrite auto ones
        existing = self.labels.get(label.address)
        if existing and existing.source == "manual" and label.source != "manual":
            log.info("label_skip_manual_exists", address=label.address[:10])
            return False
        self.labels[label.address] = label
        return is_new

    def remove(self, address: str) -> bool:
        return self.labels.pop(address.lower(), None) is not None

    def get(self, address: str) -> Label | None:
        return self.labels.get(address.lower())

    def all(self) -> list[Label]:
        return list(self.labels.values())

    def filter(self, label: str) -> list[Label]:
        return [lbl for lbl in self.labels.values() if lbl.label == label]


# ----- Import formats -----

_IMPORT_HELP = """
CSV format (header row required):
  address,label,symbol,notes
  0xabc...123,good,GOOD,sustained volume for 3 weeks
  0xdef...456,rug,RUG,drained LP on day 5
  ...

JSON format:
  [
    {"address": "0xabc...", "label": "good", "symbol": "GOOD", "notes": "..."},
    ...
  ]
"""


def import_labels_from_csv(path: Path) -> list[Label]:
    out: list[Label] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lbl = Label(
                    address=row["address"],
                    label=row["label"],
                    symbol=row.get("symbol", ""),
                    notes=row.get("notes", ""),
                    source="import",
                )
                out.append(lbl)
            except (KeyError, ValueError) as e:
                log.warning("label_import_skip", error=str(e), row=row)
    return out


def import_labels_from_json(path: Path) -> list[Label]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "labels" in data:
        data = data["labels"]
    out: list[Label] = []
    for entry in data:
        try:
            entry = dict(entry)
            entry.setdefault("source", "import")
            out.append(Label.from_dict(entry))
        except (KeyError, ValueError) as e:
            log.warning("label_import_skip", error=str(e), entry=entry)
    return out


# ----- Heuristic auto-labeling -----

# Tokens with 24h price drop ≥ this % are labeled "rug"
AUTO_RUG_PRICE_DROP_24H_PCT = -80.0
# Tokens with 24h volume ≥ $50k AND 24h change in [-20%, +200%] are "good"
AUTO_GOOD_VOLUME_24H_USD = 50_000.0
AUTO_GOOD_CHANGE_24H_MIN = -20.0
AUTO_GOOD_CHANGE_24H_MAX = 200.0


def auto_label_from_pair(symbol: str, address: str, change_24h: float, vol_24h: float) -> Label | None:
    """Heuristically label a single token by its current price action.

    Returns None if the heuristic isn't confident (label would be misleading).
    """
    from datetime import datetime
    now = datetime.now(UTC).isoformat()

    if change_24h <= AUTO_RUG_PRICE_DROP_24H_PCT:
        return Label(
            address=address,
            label="rug",
            symbol=symbol,
            notes=f"auto: 24h change {change_24h:.1f}% <= {AUTO_RUG_PRICE_DROP_24H_PCT}%",
            source="auto",
            confidence=0.7,
            labeled_at=now,
        )
    if (
        vol_24h >= AUTO_GOOD_VOLUME_24H_USD
        and AUTO_GOOD_CHANGE_24H_MIN <= change_24h <= AUTO_GOOD_CHANGE_24H_MAX
    ):
        return Label(
            address=address,
            label="good",
            symbol=symbol,
            notes=f"auto: 24h vol ${vol_24h:,.0f}, change {change_24h:+.1f}%",
            source="auto",
            confidence=0.6,
            labeled_at=now,
        )
    return None


__all__ = [
    "VALID_LABELS",
    "_IMPORT_HELP",
    "Label",
    "LabelStore",
    "auto_label_from_pair",
    "import_labels_from_csv",
    "import_labels_from_json",
]
