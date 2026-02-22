# PostgreSQL Migration Plan

## Overview

This document outlines the complete migration from JSON file storage to PostgreSQL (Supabase) for the Sorare MLS Auction Tracker.

### Current State
- **Storage**: JSON files in `data/history/` (one file per player)
- **Players**: `players.yaml` (YAML configuration)
- **Data size**: 101 players, ~33K lines (~3-5MB estimate)

### Target State
- **Storage**: PostgreSQL database (Supabase)
- **Players**: Database table (migrated from YAML)
- **Deployment**: Streamlit Cloud with external PostgreSQL

---

## Phase 1: Supabase Setup

### 1.1 Create Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign up
2. Create a new project:
   - Name: `sorare-auctions` (or your preference)
   - Database Password: Choose a strong password
   - Region: Select closest to you
3. Wait for project to initialize (~2 minutes）

### 1.2 Get Connection Details

From Supabase dashboard:
- **Host**: `db.XXXXXX.supabase.co` (found in Settings → Database)
- **Port**: `5432`
- **User**: `postgres`
- **Password**: (your chosen password)
- **Database**: `postgres`

### 1.3 Database Schema

Connect to Supabase SQL Editor and run:

```sql
-- Players table (migrated from players.yaml)
CREATE TABLE players (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(255) UNIQUE NOT NULL,
    team VARCHAR(10) NOT NULL,
    position VARCHAR(3) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auctions table
CREATE TABLE auctions (
    id SERIAL PRIMARY KEY,
    player_slug VARCHAR(255) NOT NULL REFERENCES players(slug),
    timestamp TIMESTAMPTZ NOT NULL,
    usd REAL,
    eur REAL,
    gbp REAL,
    eth REAL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_slug, timestamp)
);

-- Indexes for fast queries
CREATE INDEX idx_auctions_player_time ON auctions(player_slug, timestamp);
CREATE INDEX idx_auctions_time ON auctions(timestamp);
CREATE INDEX idx_players_slug ON players(slug);
CREATE INDEX idx_players_position ON players(position);
CREATE INDEX idx_players_team ON players(team);
```

---

## Phase 2: Dependencies

Update `requirements.txt`:

```
requests
pyyaml
pandas
streamlit==1.54.0
altair>=5.0.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
```

---

## Phase 3: Database Module

Create `db.py`:

```python
"""Database connection and operations for PostgreSQL."""

import os
from datetime import datetime
from typing import Generator

from sqlalchemy import create_engine, Column, Integer, String, Real, DateTime, UniqueConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, relationship


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    slug = Column(String(255), unique=True, nullable=False)
    team = Column(String(10), nullable=False)
    position = Column(String(3), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    auctions = relationship("Auction", back_populates="player")


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        UniqueConstraint("player_slug", "timestamp", name="uq_auction_player_time"),
    )

    id = Column(Integer, primary_key=True)
    player_slug = Column(String(255), ForeignKey("players.slug"), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    usd = Column(Real)
    eur = Column(Real)
    gbp = Column(Real)
    eth = Column(Real)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    player = relationship("Player", back_populates="auctions")


DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
else:
    engine = None
    SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    """Yield a database session."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL not set")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables."""
    Base.metadata.create_all(bind=engine)
```

---

## Phase 4: Migration Script

Create `scripts/migrate_to_sql.py`:

```python
"""One-time migration script: JSON + YAML → PostgreSQL."""

import json
import os
from datetime import datetime

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import from db.py
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import Base, Player, Auction, DATABASE_URL


def migrate_players(session, players_yaml_path: str) -> int:
    """Migrate players from YAML to database."""
    with open(players_yaml_path, "r") as f:
        data = yaml.safe_load(f)

    position_map = {"gk": "gk", "df": "df", "mf": "mf", "fw": "fw"}
    count = 0

    for pos, players in data.items():
        if not players:
            continue
        for p in players:
            existing = session.query(Player).filter_by(slug=p["slug"]).first()
            if not existing:
                player = Player(
                    slug=p["slug"],
                    team=p["team"],
                    position=position_map.get(pos, pos),
                )
                session.add(player)
                count += 1

    session.commit()
    return count


def migrate_auctions(session, history_dir: str) -> tuple[int, int]:
    """Migrate auction history from JSON files to database."""
    total_files = 0
    total_auctions = 0

    for filename in os.listdir(history_dir):
        if not filename.endswith(".json"):
            continue

        slug = filename[:-5]  # Remove .json
        filepath = os.path.join(history_dir, filename)

        with open(filepath, "r") as f:
            data = json.load(f)

        total_files += 1

        for timestamp_str, prices in data.items():
            # Parse timestamp
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            # Check if already exists
            existing = session.query(Auction).filter_by(
                player_slug=slug, timestamp=timestamp
            ).first()

            if not existing:
                auction = Auction(
                    player_slug=slug,
                    timestamp=timestamp,
                    usd=prices.get("usd"),
                    eur=prices.get("eur"),
                    gbp=prices.get("gbp"),
                    eth=prices.get("eth"),
                )
                session.add(auction)
                total_auctions += 1

        if total_files % 10 == 0:
            session.commit()
            print(f"  Processed {total_files} files, {total_auctions} auctions...")

    session.commit()
    return total_files, total_auctions


def main():
    # Get DATABASE_URL
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        print("Set it with: export DATABASE_URL='postgresql://user:pass@host:5432/db'")
        return

    # Create engine and tables
    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Migrate players
    print("\n=== Migrating Players ===")
    players_path = os.path.join(base_dir, "players.yaml")
    player_count = migrate_players(session, players_path)
    print(f"Migrated {player_count} players")

    # Migrate auctions
    print("\n=== Migrating Auctions ===")
    history_dir = os.path.join(base_dir, "data", "history")
    files, auctions = migrate_auctions(session, history_dir)
    print(f"Migrated {files} files with {auctions} auctions")

    session.close()
    print("\n=== Migration Complete ===")


if __name__ == "__main__":
    main()
```

### Run Migration

```bash
# Set DATABASE_URL (replace with your Supabase credentials)
export DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@db.XXXXXX.supabase.co:5432/postgres"

# Run migration
python scripts/migrate_to_sql.py
```

---

## Phase 5: Update fetch_auctions.py

### Changes to make:

| Location | Old (JSON) | New (SQL) |
|----------|------------|-----------|
| `load_history(path)` | Read JSON file | Query `Auction` table |
| `save_history(path, history)` | Write JSON file | `INSERT` or `UPDATE` auctions |
| `_process_player_results()` | File path operations | Database operations |
| `_backfill_player()` | File path operations | Database operations |

### Key function changes:

```python
def load_auctions(slug: str) -> dict[str, dict]:
    """Load auction history from database."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL not set")
    
    session = SessionLocal()
    try:
        auctions = session.query(Auction).filter_by(player_slug=slug).all()
        result = {}
        for a in auctions:
            ts = a.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            result[ts] = {"usd": a.usd, "eur": a.eur, "gbp": a.gbp, "eth": a.eth}
        return result
    finally:
        session.close()


def save_auction(slug: str, timestamp: datetime, prices: dict) -> bool:
    """Save a single auction to database. Returns True if new."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL not set")
    
    session = SessionLocal()
    try:
        existing = session.query(Auction).filter_by(
            player_slug=slug, timestamp=timestamp
        ).first()
        
        if existing:
            return False
        
        auction = Auction(
            player_slug=slug,
            timestamp=timestamp,
            usd=prices.get("usd"),
            eur=prices.get("eur"),
            gbp=prices.get("gbp"),
            eth=prices.get("eth"),
        )
        session.add(auction)
        session.commit()
        return True
    finally:
        session.close()
```

---

## Phase 6: Update app.py

### Changes to make:

| Location | Old (JSON) | New (SQL) |
|----------|------------|-----------|
| `_load_player_history(slug, currency)` | Read JSON file | Query `Auction` table |
| `_load_and_prepare(position)` | Load from YAML + JSON | Query `Player` + `Auction` tables |
| Data loading | File I/O | Database queries |

### Key function changes:

```python
def _load_player_history(slug: str, currency_key: str) -> tuple[list[str], list[float | None]]:
    """Load a player's auction history from database."""
    if SessionLocal is None:
        return [], []
    
    session = SessionLocal()
    try:
        auctions = session.query(Auction).filter_by(player_slug=slug).order_by(Auction.timestamp).all()
        
        dates = []
        prices = []
        for a in auctions:
            dates.append(a.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if currency_key == "usd":
                prices.append(a.usd)
            elif currency_key == "eur":
                prices.append(a.eur)
            elif currency_key == "gbp":
                prices.append(a.gbp)
            elif currency_key == "eth":
                prices.append(a.eth)
        return dates, prices
    finally:
        session.close()


def _load_and_prepare(position: str) -> tuple[pd.DataFrame, dict] | None:
    """Load players and auctions from database."""
    if SessionLocal is None:
        return None
    
    session = SessionLocal()
    try:
        # Get players for position
        players = session.query(Player).filter_by(position=position).all()
        if not players:
            return None
        
        currency_key = currency_cfg["key"]
        
        rows = []
        for p in players:
            dates, prices = _load_player_history(p.slug, currency_key)
            # ... rest of logic similar to current ...
        
        # ... build DataFrame ...
        return result, player_details
    finally:
        session.close()
```

### Remove from app.py:
- `HISTORY_DIR` constant
- `PLAYERS_PATH` constant
- JSON file reading/writing code

---

## Phase 7: Update backfill.py

Similar changes to fetch_auctions.py - replace JSON file operations with database operations.

### Key changes:
- Remove `load_history()` and `save_history()` calls
- Use `save_auction()` for each new auction
- Query players from database instead of YAML

---

## Phase 8: Local Testing

### Test locally:

```bash
# Set DATABASE_URL
export DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@db.XXXXXX.supabase.co:5432/postgres"

# Run Streamlit
streamlit run app.py

# Test fetching new data
python fetch_auctions.py
```

---

## Phase 9: Streamlit Cloud Deployment

### 1. Add Secrets

In Streamlit Cloud dashboard:
1. Go to your app settings
2. Navigate to **Secrets**
3. Add:

```toml
[secrets]
DATABASE_URL = "postgresql://postgres:YOUR_PASSWORD@db.XXXXXX.supabase.co:5432/postgres"
```

### 2. Deploy

1. Push changes to GitHub
2. Streamlit Cloud automatically deploys

---

## Phase 10: Cleanup (Optional)

After verified working:

```bash
# Remove JSON files (backup first!)
rm -rf data/history/

# Optionally remove CSV generation code from fetch_auctions.py
# Optionally remove data/limited_*.csv files
```

---

## File Changes Summary

| File | Action |
|------|--------|
| `requirements.txt` | Add `sqlalchemy`, `psycopg2-binary` |
| `db.py` | CREATE - Database models and connection |
| `scripts/migrate_to_sql.py` | CREATE - One-time migration script |
| `fetch_auctions.py` | MODIFY - Replace JSON I/O with DB |
| `app.py` | MODIFY - Replace JSON I/O with DB |
| `backfill.py` | MODIFY - Replace JSON I/O with DB |

---

## Rollback Plan

If issues occur:

1. **Keep JSON files** - They're still in git as backup
2. **Revert code changes** - Roll back to last working commit
3. **Test locally** - Ensure JSON version still works
4. **Debug** - Check DATABASE_URL, schema, queries

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `DATABASE_URL not set` | Set env var or check Streamlit secrets |
| Connection timeout | Check Supabase project status, try again |
| Table not found | Run migration script to create tables |
| Duplicate key errors | Schema has `UNIQUE` constraint - expected for updates |

### Check Data

```sql
-- Count players
SELECT COUNT(*) FROM players;

-- Count auctions
SELECT COUNT(*) FROM auctions;

-- Sample query
SELECT p.slug, p.team, COUNT(a.id) as auction_count
FROM players p
LEFT JOIN auctions a ON p.slug = a.player_slug
GROUP BY p.slug, p.team
ORDER BY auction_count DESC
LIMIT 10;
```
