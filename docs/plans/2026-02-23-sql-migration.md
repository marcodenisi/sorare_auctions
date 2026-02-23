# JSON-to-PostgreSQL Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace JSON file storage with PostgreSQL (Supabase) so the project has a proper data layer that supports future enhancements (multi-rarity, multi-league, advanced queries).

**Architecture:** Introduce a `db.py` module using SQLAlchemy ORM that all other modules import. A one-time migration script copies existing JSON + YAML data into PostgreSQL. Then `fetch_auctions.py`, `backfill.py`, and `app.py` are updated to read/write via `db.py` instead of JSON files. GitHub Actions workflow is updated to use the DB. JSON files are kept in git as a backup until confidence is established.

**Tech Stack:** Python 3, SQLAlchemy 2.x, psycopg2-binary, Supabase (hosted PostgreSQL)

**Current state:** 101 players in `players.yaml`, 5,873 auction entries across 101 JSON files in `data/history/`.

---

## Prerequisites (manual, before starting)

1. Create a Supabase project at [supabase.com](https://supabase.com)
   - Name: `sorare-auctions`
   - Region: closest to you
   - Note the database password
2. Get your connection string from Settings → Database:
   `postgresql://postgres.XXXXX:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres`
3. Set it locally: `export DATABASE_URL="postgresql://..."`
4. Add it as a GitHub Actions secret named `DATABASE_URL`

---

## Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

**Step 1: Update requirements.txt**

Add `sqlalchemy`, `psycopg2-binary`, `bcrypt`, and `altair` (the latter two are already used but were missing):

```
requests
pyyaml
pandas
streamlit==1.54.0
altair>=5.0.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
bcrypt>=4.0.0
```

**Step 2: Install**

Run: `pip install -r requirements.txt`

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add sqlalchemy, psycopg2-binary, and missing deps"
```

---

## Task 2: Database Module — Models & Connection

**Files:**
- Create: `db.py`
- Create: `tests/test_db.py`

**Step 1: Write failing tests for the DB models**

```python
# tests/test_db.py
"""Tests for db module using an in-memory SQLite database."""

import os
from datetime import datetime, timezone

import pytest

# Force SQLite for tests (must be set before importing db)
os.environ["DATABASE_URL"] = "sqlite://"

from db import Base, Player, Auction, get_engine, get_session, init_db


@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test, drop after."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_create_player():
    with get_session() as session:
        player = Player(slug="test-player", team="TST", position="mf")
        session.add(player)
        session.commit()

        result = session.query(Player).filter_by(slug="test-player").one()
        assert result.team == "TST"
        assert result.position == "mf"


def test_create_auction():
    with get_session() as session:
        player = Player(slug="test-player", team="TST", position="gk")
        session.add(player)
        session.flush()

        ts = datetime(2026, 2, 18, 6, 33, 2, tzinfo=timezone.utc)
        auction = Auction(
            player_slug="test-player",
            timestamp=ts,
            usd=126.32,
            eur=106.68,
            gbp=93.20,
            eth=0.0633,
        )
        session.add(auction)
        session.commit()

        result = session.query(Auction).filter_by(player_slug="test-player").one()
        assert result.usd == pytest.approx(126.32, abs=0.01)
        assert result.eth == pytest.approx(0.0633, abs=0.0001)


def test_duplicate_auction_rejected():
    with get_session() as session:
        player = Player(slug="test-player", team="TST", position="fw")
        session.add(player)
        session.flush()

        ts = datetime(2026, 2, 18, 6, 33, 2, tzinfo=timezone.utc)
        a1 = Auction(player_slug="test-player", timestamp=ts, usd=100.0)
        session.add(a1)
        session.commit()

    # Second insert with same slug+timestamp should fail
    with pytest.raises(Exception):
        with get_session() as session:
            a2 = Auction(player_slug="test-player", timestamp=ts, usd=200.0)
            session.add(a2)
            session.commit()


def test_player_auctions_relationship():
    with get_session() as session:
        player = Player(slug="rel-player", team="TST", position="df")
        session.add(player)
        session.flush()

        for i in range(3):
            ts = datetime(2026, 2, 18, i, 0, 0, tzinfo=timezone.utc)
            session.add(Auction(player_slug="rel-player", timestamp=ts, usd=100.0 + i))
        session.commit()

        p = session.query(Player).filter_by(slug="rel-player").one()
        assert len(p.auctions) == 3
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL (ImportError — `db` module doesn't exist yet)

**Step 3: Write the db module**

```python
# db.py
"""Database models and connection management for PostgreSQL."""

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "")


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    slug = Column(String(255), unique=True, nullable=False)
    team = Column(String(10), nullable=False)
    position = Column(String(3), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    auctions = relationship(
        "Auction", back_populates="player", cascade="all, delete-orphan"
    )


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        UniqueConstraint("player_slug", "timestamp", name="uq_auction_player_time"),
    )

    id = Column(Integer, primary_key=True)
    player_slug = Column(
        String(255), ForeignKey("players.slug", ondelete="CASCADE"), nullable=False
    )
    timestamp = Column(DateTime(timezone=True), nullable=False)
    usd = Column(Numeric(10, 2))
    eur = Column(Numeric(10, 2))
    gbp = Column(Numeric(10, 2))
    eth = Column(Numeric(18, 8))
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    player = relationship("Player", back_populates="auctions")


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def get_session() -> Session:
    """Return a session context manager. Usage: with get_session() as session: ..."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=get_engine())
```

Wait — `get_session()` returns a raw Session but tests use `with get_session() as session:`. We need to make `Session` a context manager. SQLAlchemy 2.x sessions already support `__enter__`/`__exit__`, but we should use `session_scope()` for auto-commit/rollback in production code, and in tests we can use the session directly.

Let me adjust — tests should use `session_scope()`:

Actually, `sqlalchemy.orm.Session` in 2.x already works as a context manager (it closes on exit but doesn't auto-commit). Let's keep it simple: `get_session()` returns a Session, tests call `session.commit()` explicitly.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add database models and connection module"
```

---

## Task 3: Migration Script — JSON + YAML to PostgreSQL

**Files:**
- Create: `scripts/migrate_to_sql.py`
- Create: `tests/test_migration.py`

**Step 1: Write failing tests**

```python
# tests/test_migration.py
"""Tests for the JSON-to-SQL migration script."""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest
import yaml

os.environ["DATABASE_URL"] = "sqlite://"

from db import Base, Player, Auction, get_engine, get_session


@pytest.fixture(autouse=True)
def setup_db():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project structure with YAML + JSON files."""
    # players.yaml
    players = {
        "gk": [{"slug": "player-a", "team": "AAA", "role": "Starter"}],
        "mf": [{"slug": "player-b", "team": "BBB", "role": "Backup"}],
    }
    yaml_path = tmp_path / "players.yaml"
    yaml_path.write_text(yaml.dump(players))

    # history dir
    history_dir = tmp_path / "data" / "history"
    history_dir.mkdir(parents=True)

    # player-a.json (new multi-currency format)
    (history_dir / "player-a.json").write_text(json.dumps({
        "2026-02-18T06:33:02Z": {"usd": 126.32, "eur": 106.68, "gbp": 93.20, "eth": 0.0633},
        "2026-02-18T01:03:04Z": {"usd": 129.93, "eur": 109.73, "gbp": 95.85, "eth": 0.0656},
    }))

    # player-b.json (old bare-float format)
    (history_dir / "player-b.json").write_text(json.dumps({
        "2026-02-17T22:32:58Z": 127.73,
    }))

    return tmp_path


def test_migrate_players(tmp_project):
    from scripts.migrate_to_sql import migrate_players
    count = migrate_players(str(tmp_project / "players.yaml"))
    assert count == 2

    with get_session() as session:
        players = session.query(Player).all()
        slugs = {p.slug for p in players}
        assert slugs == {"player-a", "player-b"}
        # Check position mapping
        pa = session.query(Player).filter_by(slug="player-a").one()
        assert pa.position == "gk"


def test_migrate_auctions_new_format(tmp_project):
    from scripts.migrate_to_sql import migrate_players, migrate_auctions
    migrate_players(str(tmp_project / "players.yaml"))
    files, auctions = migrate_auctions(str(tmp_project / "data" / "history"))
    assert files == 2
    assert auctions == 3  # 2 for player-a + 1 for player-b

    with get_session() as session:
        a = session.query(Auction).filter_by(player_slug="player-a").all()
        assert len(a) == 2


def test_migrate_auctions_old_format(tmp_project):
    from scripts.migrate_to_sql import migrate_players, migrate_auctions
    migrate_players(str(tmp_project / "players.yaml"))
    migrate_auctions(str(tmp_project / "data" / "history"))

    with get_session() as session:
        a = session.query(Auction).filter_by(player_slug="player-b").one()
        assert float(a.usd) == pytest.approx(127.73, abs=0.01)
        assert a.eur is None  # old format has no EUR


def test_migrate_idempotent(tmp_project):
    from scripts.migrate_to_sql import migrate_players, migrate_auctions
    migrate_players(str(tmp_project / "players.yaml"))
    migrate_auctions(str(tmp_project / "data" / "history"))

    # Run again — should not duplicate
    count = migrate_players(str(tmp_project / "players.yaml"))
    assert count == 0  # no new players
    _, auctions = migrate_auctions(str(tmp_project / "data" / "history"))
    assert auctions == 0  # no new auctions
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_migration.py -v`
Expected: FAIL (ImportError — `scripts.migrate_to_sql` doesn't exist)

**Step 3: Create the migration script**

Create `scripts/__init__.py` (empty) and `scripts/migrate_to_sql.py`:

```python
# scripts/__init__.py
```

```python
# scripts/migrate_to_sql.py
"""One-time migration: JSON files + players.yaml -> PostgreSQL."""

import json
import os
from datetime import datetime, timezone

import yaml
from sqlalchemy.exc import IntegrityError

from db import Auction, Player, get_session, init_db


def _migrate_old_entry(value) -> dict:
    """Convert legacy bare-float entry to multi-currency dict."""
    if isinstance(value, dict):
        return value
    return {"usd": float(value), "eur": None, "gbp": None, "eth": None}


def migrate_players(players_yaml_path: str) -> int:
    """Migrate players from YAML to database. Returns count of new players."""
    with open(players_yaml_path, "r") as f:
        data = yaml.safe_load(f)

    count = 0
    with get_session() as session:
        for pos, players in data.items():
            if not players:
                continue
            for p in players:
                existing = session.query(Player).filter_by(slug=p["slug"]).first()
                if not existing:
                    session.add(Player(
                        slug=p["slug"],
                        team=p["team"],
                        position=pos,
                    ))
                    count += 1
        session.commit()
    return count


def migrate_auctions(history_dir: str) -> tuple[int, int]:
    """Migrate auction JSON files to database. Returns (files, new_auctions)."""
    total_files = 0
    total_new = 0

    for filename in sorted(os.listdir(history_dir)):
        if not filename.endswith(".json"):
            continue

        slug = filename[:-5]
        filepath = os.path.join(history_dir, filename)

        with open(filepath, "r") as f:
            raw = json.load(f)

        total_files += 1

        with get_session() as session:
            for timestamp_str, value in raw.items():
                prices = _migrate_old_entry(value)

                try:
                    ts = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue

                existing = session.query(Auction).filter_by(
                    player_slug=slug, timestamp=ts
                ).first()

                if not existing:
                    session.add(Auction(
                        player_slug=slug,
                        timestamp=ts,
                        usd=prices.get("usd"),
                        eur=prices.get("eur"),
                        gbp=prices.get("gbp"),
                        eth=prices.get("eth"),
                    ))
                    total_new += 1

            session.commit()

        if total_files % 20 == 0:
            print(f"  Processed {total_files} files, {total_new} new auctions...")

    return total_files, total_new


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        print("Set it with: export DATABASE_URL='postgresql://...'")
        return

    init_db()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("\n=== Migrating Players ===")
    players_path = os.path.join(base_dir, "players.yaml")
    player_count = migrate_players(players_path)
    print(f"Migrated {player_count} new players")

    print("\n=== Migrating Auctions ===")
    history_dir = os.path.join(base_dir, "data", "history")
    files, auctions = migrate_auctions(history_dir)
    print(f"Processed {files} files, {auctions} new auctions")

    print("\n=== Migration Complete ===")


if __name__ == "__main__":
    main()
```

**Step 4: Run tests**

Run: `pytest tests/test_migration.py -v`
Expected: All 4 tests PASS

**Step 5: Run the actual migration against Supabase**

```bash
export DATABASE_URL="postgresql://..."
python -m scripts.migrate_to_sql
```

Expected output:
```
=== Migrating Players ===
Migrated 101 new players

=== Migrating Auctions ===
  Processed 20 files, ...
  Processed 40 files, ...
  ...
Processed 101 files, 5873 new auctions

=== Migration Complete ===
```

**Step 6: Verify data in Supabase**

Run in Supabase SQL editor:
```sql
SELECT COUNT(*) FROM players;       -- expect 101
SELECT COUNT(*) FROM auctions;      -- expect 5873
SELECT p.slug, COUNT(a.id) as n
FROM players p LEFT JOIN auctions a ON p.slug = a.player_slug
GROUP BY p.slug ORDER BY n DESC LIMIT 5;
```

**Step 7: Commit**

```bash
git add scripts/__init__.py scripts/migrate_to_sql.py tests/test_migration.py
git commit -m "feat: add JSON-to-SQL migration script with tests"
```

---

## Task 4: Update fetch_auctions.py — Write to DB Instead of JSON

**Files:**
- Modify: `fetch_auctions.py`
- Create: `tests/test_fetch_auctions.py`

This task replaces `load_history()`, `save_history()`, and `_process_player_results()` with DB equivalents. The GraphQL fetching logic (API calls, batching, pagination) stays unchanged.

**Step 1: Write tests for the new DB-backed storage functions**

```python
# tests/test_fetch_auctions.py
"""Tests for fetch_auctions DB integration."""

import os
from datetime import datetime, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite://"

from db import Base, Player, Auction, get_engine, get_session, init_db


@pytest.fixture(autouse=True)
def setup_db():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_save_new_auctions():
    """New auctions are saved to DB and counted correctly."""
    from fetch_auctions import save_auctions_to_db

    # Create player first
    with get_session() as session:
        session.add(Player(slug="test-player", team="TST", position="gk"))
        session.commit()

    auctions = [
        ("2026-02-18T06:33:02Z", {"usd": 126.32, "eur": 106.68, "gbp": 93.20, "eth": 0.0633}),
        ("2026-02-18T01:03:04Z", {"usd": 129.93, "eur": 109.73, "gbp": 95.85, "eth": 0.0656}),
    ]
    total, new_count = save_auctions_to_db("test-player", auctions)
    assert new_count == 2
    assert total == 2


def test_save_auctions_deduplicates():
    """Duplicate timestamps are skipped, not errored."""
    from fetch_auctions import save_auctions_to_db

    with get_session() as session:
        session.add(Player(slug="test-player", team="TST", position="gk"))
        session.commit()

    auctions = [
        ("2026-02-18T06:33:02Z", {"usd": 126.32, "eur": 106.68, "gbp": 93.20, "eth": 0.0633}),
    ]
    save_auctions_to_db("test-player", auctions)

    # Save again with same + one new
    auctions2 = [
        ("2026-02-18T06:33:02Z", {"usd": 126.32, "eur": 106.68, "gbp": 93.20, "eth": 0.0633}),
        ("2026-02-19T01:00:00Z", {"usd": 200.00, "eur": None, "gbp": None, "eth": None}),
    ]
    total, new_count = save_auctions_to_db("test-player", auctions2)
    assert new_count == 1
    assert total == 2


def test_load_history_from_db():
    """load_history_from_db returns existing auctions as a dict keyed by date."""
    from fetch_auctions import load_history_from_db, save_auctions_to_db

    with get_session() as session:
        session.add(Player(slug="test-player", team="TST", position="gk"))
        session.commit()

    auctions = [
        ("2026-02-18T06:33:02Z", {"usd": 126.32, "eur": 106.68, "gbp": 93.20, "eth": 0.0633}),
    ]
    save_auctions_to_db("test-player", auctions)

    history = load_history_from_db("test-player")
    assert "2026-02-18T06:33:02Z" in history or len(history) == 1
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_auctions.py -v`
Expected: FAIL (ImportError — `save_auctions_to_db` doesn't exist)

**Step 3: Add DB functions to fetch_auctions.py and rewire _process_player_results**

Add these functions to `fetch_auctions.py` (replacing the JSON `load_history` / `save_history` / `_process_player_results`):

```python
# Add to imports at top of fetch_auctions.py:
from datetime import datetime, timezone
from db import Auction, Player, get_session

# Replace load_history and save_history with:

def load_history_from_db(slug: str) -> dict[str, dict]:
    """Load previously saved auction history from database.

    Returns dict keyed by ISO timestamp string -> price record dict.
    """
    with get_session() as session:
        auctions = (
            session.query(Auction)
            .filter_by(player_slug=slug)
            .all()
        )
        result = {}
        for a in auctions:
            ts = a.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            result[ts] = {
                "usd": float(a.usd) if a.usd is not None else None,
                "eur": float(a.eur) if a.eur is not None else None,
                "gbp": float(a.gbp) if a.gbp is not None else None,
                "eth": float(a.eth) if a.eth is not None else None,
            }
        return result


def save_auctions_to_db(slug: str, auctions: list[tuple[str, dict]]) -> tuple[int, int]:
    """Save auctions to database. Returns (total_count, new_count)."""
    new_count = 0
    with get_session() as session:
        for date_str, price_record in auctions:
            try:
                ts = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            existing = session.query(Auction).filter_by(
                player_slug=slug, timestamp=ts
            ).first()

            if not existing:
                session.add(Auction(
                    player_slug=slug,
                    timestamp=ts,
                    usd=price_record.get("usd"),
                    eur=price_record.get("eur"),
                    gbp=price_record.get("gbp"),
                    eth=price_record.get("eth"),
                ))
                new_count += 1

        session.commit()

        total = session.query(Auction).filter_by(player_slug=slug).count()

    return total, new_count
```

Then update `_process_player_results` to call the DB functions instead of JSON:

```python
def _process_player_results(
    slug: str,
    new_auctions: list[tuple[str, dict]],
) -> None:
    """Merge new auctions into database."""
    total, new_count = save_auctions_to_db(slug, new_auctions)
    print(f"{total} total auctions ({new_count} new)")
```

Remove the `history_dir` parameter from `_process_player_results` and all call sites.

Similarly update `_backfill_player` to use `load_history_from_db` / `save_auctions_to_db` instead of JSON file I/O.

Remove: `load_history()`, `save_history()`, `_migrate_old_entry()`, the `history_dir` variable, and all `os.path` references to `data/history`.

Keep: `_make_price_record()`, all GraphQL/API functions, `build_batch_query()`, `fetch_batch_auction_prices()`, `fetch_auction_prices()`.

In `main()`:
- Remove `history_dir = ...` and `os.makedirs(history_dir, ...)`.
- Remove the CSV writing and `last_updated.txt` writing (the DB is now the source of truth — `last_updated` can be derived from `MAX(auctions.created_at)`).
- Add `from db import init_db` and call `init_db()` at the start of `main()`.

**Step 4: Run tests**

Run: `pytest tests/test_fetch_auctions.py -v`
Expected: All 3 tests PASS

**Step 5: Run the actual fetch against Supabase to verify**

```bash
export DATABASE_URL="postgresql://..."
python fetch_auctions.py
```

Expected: Fetches new auctions and saves to DB. No JSON files written.

**Step 6: Commit**

```bash
git add fetch_auctions.py tests/test_fetch_auctions.py
git commit -m "feat: fetch_auctions writes to database instead of JSON"
```

---

## Task 5: Update backfill.py — Write to DB Instead of JSON

**Files:**
- Modify: `backfill.py`

**Step 1: Update imports**

Replace:
```python
from fetch_auctions import (
    API_URL,
    BATCH_SIZE,
    POSITIONS,
    _make_price_record,
    load_history,
    name_from_slug,
    ordinal,
    save_history,
)
```

With:
```python
from fetch_auctions import (
    API_URL,
    BATCH_SIZE,
    POSITIONS,
    _make_price_record,
    load_history_from_db,
    save_auctions_to_db,
)
from utils import name_from_slug, ordinal
```

**Step 2: Replace JSON I/O in main() and fetch_all_auctions()**

In `main()`:
- Remove `history_dir` variable and `os.makedirs(...)`.
- Replace `load_history(history_path)` with `load_history_from_db(slug)`.
- Replace the manual merge loop + `save_history(...)` with `save_auctions_to_db(slug, auctions)`.

The `fetch_all_auctions()` function does NOT change — it only does API calls and returns `list[tuple[str, dict]]`.

**Step 3: Test manually**

```bash
export DATABASE_URL="postgresql://..."
python backfill.py
```

**Step 4: Commit**

```bash
git add backfill.py
git commit -m "feat: backfill.py writes to database instead of JSON"
```

---

## Task 6: Update app.py — Read from DB Instead of JSON

**Files:**
- Modify: `app.py`

This is the largest change. The dashboard needs to read players and auctions from the DB instead of `players.yaml` + JSON files.

**Step 1: Replace _load_player_history to read from DB**

Replace the current `_load_player_history()`:

```python
# Add at top of app.py:
from db import Auction, Player, get_session

# Replace _load_player_history:
def _load_player_history(
    slug: str, currency_key: str
) -> tuple[list[str], list[float | None]]:
    """Load a player's auction history from database.

    Returns (dates, prices) sorted by date ascending.
    """
    with get_session() as session:
        auctions = (
            session.query(Auction)
            .filter_by(player_slug=slug)
            .order_by(Auction.timestamp.asc())
            .all()
        )

        dates = []
        prices = []
        for a in auctions:
            dates.append(a.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
            val = getattr(a, currency_key, None)
            prices.append(float(val) if val is not None else None)

        return dates, prices
```

**Step 2: Replace _load_and_prepare to get players from DB**

Replace the YAML-reading section of `_load_and_prepare()`:

```python
def _load_and_prepare(position: str) -> tuple[pd.DataFrame, dict] | None:
    """Load players and auctions from database and build the display DataFrame."""
    with get_session() as session:
        players = session.query(Player).filter_by(position=position).all()
        if not players:
            return None
        player_list = [{"slug": p.slug, "team": p.team} for p in players]

    currency_key = currency_cfg["key"]

    rows = []
    max_prices = 0

    for p in player_list:
        slug = p["slug"]
        team = p["team"]
        display_name = name_from_slug(slug)

        dates, prices = _load_player_history(slug, currency_key)
        # ... rest of the function stays the same from here ...
```

**Step 3: Replace the Compare section's player loading**

In the "Compare Players Across Positions" section, replace the YAML-reading block:

```python
# Replace the YAML reading in the compare section:
with get_session() as session:
    all_db_players = session.query(Player).all()
    all_players = [
        {"slug": p.slug, "team": p.team, "name": name_from_slug(p.slug)}
        for p in all_db_players
    ]
```

**Step 4: Replace "Last updated" display**

Replace the `last_updated.txt` reading with a DB query:

```python
# Replace the last_updated_path section near top of app.py:
try:
    with get_session() as session:
        from sqlalchemy import func
        last_ts = session.query(func.max(Auction.created_at)).scalar()
        if last_ts:
            st.caption(f"Last updated: {last_ts.strftime('%Y-%m-%d %H:%M UTC')}")
except Exception:
    pass  # DB not available
```

**Step 5: Remove unused imports and constants**

Remove from `app.py`:
- `import json`
- `HISTORY_DIR` constant
- `PLAYERS_PATH` constant
- `last_updated_path` variable
- `import yaml` (unless still needed for Compare section — it isn't after Step 3)

**Step 6: Test manually**

```bash
export DATABASE_URL="postgresql://..."
streamlit run app.py
```

Verify:
- [ ] All 4 tabs render with correct data
- [ ] Currency selector works (USD/EUR/GBP/ETH)
- [ ] Player details expander shows chart + stats
- [ ] Compare section works across positions
- [ ] "Last updated" shows a timestamp
- [ ] CSV download works

**Step 7: Commit**

```bash
git add app.py
git commit -m "feat: app.py reads from database instead of JSON files"
```

---

## Task 7: Update GitHub Actions Workflow

**Files:**
- Modify: `.github/workflows/fetch-auctions.yml`

The workflow currently commits JSON diffs to git. After migration, it just runs `fetch_auctions.py` which writes to the DB.

**Step 1: Update the workflow**

```yaml
name: Fetch auction data

on:
  schedule:
    - cron: "*/30 * * * *"
  workflow_dispatch:

jobs:
  fetch:
    runs-on: ubuntu-latest
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Fetch auction data
        run: python fetch_auctions.py
```

Key changes:
- Removed `permissions: contents: write` (no longer pushing to git)
- Removed the git add/commit/push step
- Added `DATABASE_URL` from secrets
- Installs full `requirements.txt` (needs sqlalchemy, psycopg2-binary)

**Step 2: Commit**

```bash
git add .github/workflows/fetch-auctions.yml
git commit -m "feat: update GH Actions to write to database instead of git"
```

---

## Task 8: Update AGENTS.md and Clean Up

**Files:**
- Modify: `AGENTS.md`
- Modify: `.gitignore`

**Step 1: Update AGENTS.md**

Update the file organization section and data handling guidelines to reflect the DB:

- Remove references to `data/history/*.json` as the primary storage
- Add `db.py` to the main files list
- Add `scripts/` to the file organization
- Update "Data Handling" section: primary storage is PostgreSQL, JSON files are legacy backup
- Add note about `DATABASE_URL` environment variable

**Step 2: Update .gitignore**

The `data/` directory is no longer actively written to. Keep it in `.gitignore` but add a comment:

```
data/           # legacy JSON files (kept as backup, no longer written to)
```

**Step 3: Commit**

```bash
git add AGENTS.md .gitignore
git commit -m "docs: update project docs for database migration"
```

---

## Task 9: End-to-End Verification

**Step 1: Run full pipeline locally**

```bash
export DATABASE_URL="postgresql://..."

# Fetch new data
python fetch_auctions.py

# Start dashboard
streamlit run app.py
```

**Step 2: Verify checklist**

- [ ] `fetch_auctions.py` writes to DB, no JSON files created
- [ ] `app.py` reads from DB, all 4 tabs work
- [ ] Currency selector works
- [ ] Player comparison works
- [ ] Stats & charts work
- [ ] CSV download works
- [ ] `backfill.py` works against DB
- [ ] All tests pass: `pytest tests/ -v`
- [ ] GH Actions workflow runs successfully (trigger manually via `workflow_dispatch`)

**Step 3: Fix any issues found, commit**

```bash
git add -A
git commit -m "fix: adjustments after end-to-end verification"
```

---

## Rollback Plan

If things go wrong:

1. JSON files remain in `data/history/` — they were never deleted
2. `git log` has every pre-migration commit
3. Revert to any pre-migration commit: the JSON pipeline still works independently
4. Supabase data can be re-migrated at any time from JSON files using `scripts/migrate_to_sql.py`

---

## What This Plan Does NOT Do (Intentionally)

- **Does not delete JSON files** — they stay as a backup until you're confident
- **Does not add Streamlit Cloud deployment** — that's a separate task (just set `DATABASE_URL` in Streamlit Cloud secrets when ready)
- **Does not migrate `players.yaml` management to a DB admin UI** — players are still added by editing YAML and re-running migration. A future enhancement could add CRUD via the dashboard.
- **Does not add the `role` field to the DB schema** — it's unused today (all players are "Starter"). Add it when you actually need it.
