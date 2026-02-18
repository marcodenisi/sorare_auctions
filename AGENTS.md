# AGENTS.md - Sorare MLS Auction Tracker

## Project Overview

This is a Streamlit-based dashboard for tracking Sorare MLS Limited card auction prices. It fetches data from Sorare's GraphQL API and displays historical auction data with filtering, charts, and player comparisons.

**Main files:**
- `app.py` - Streamlit dashboard UI
- `fetch_auctions.py` - Script to fetch auction data from Sorare API
- `backfill.py` - Historical data backfill script
- `utils.py` - Shared utility functions
- `players.yaml` - Player roster configuration

---

## Build / Run Commands

### Setup
```bash
# Create virtual environment (Python 3.10+ required)
python3.11 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

### Running the Application
```bash
# Run Streamlit dashboard
streamlit run app.py

# Run auction fetcher
python fetch_auctions.py

# Run backfill script
python backfill.py --help
```

### Testing
No formal test suite exists yet. To add tests:
```bash
# Install pytest
pip install pytest

# Run all tests
pytest

# Run a single test file
pytest test_file.py

# Run tests matching a pattern
pytest -k "test_name"
```

### Linting / Type Checking
```bash
# Install linting tools
pip install ruff mypy

# Run ruff (linting)
ruff check .

# Run ruff with auto-fix
ruff check --fix .

# Run mypy (type checking)
mypy .
```

---

## Code Style Guidelines

### General Principles
- Keep functions short and focused (under 50 lines when possible)
- Use descriptive names for variables, functions, and files
- Add docstrings to all public functions
- Handle errors gracefully with informative messages

### Imports
- Standard library imports first
- Third-party imports second
- Local imports last
- Group imports by type with blank lines between groups
- Sort alphabetically within groups

```python
# Good
import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st
import yaml

from utils import name_from_slug
```

### Formatting
- Maximum line length: 100 characters
- Use 4 spaces for indentation (no tabs)
- Use blank lines sparingly to group related code
- No trailing whitespace

### Type Hints
- Use type hints for function parameters and return values
- Use `|` syntax for unions (Python 3.10+): `str | None`
- Avoid `Any` when possible

```python
# Good
def _load_player_history(slug: str, currency_key: str) -> tuple[list[str], list[float | None]]:
    ...

# Avoid
def _load_player_history(slug, currency_key):
    ...
```

### Naming Conventions
- **Functions/variables**: `snake_case` (e.g., `fetch_auctions`, `player_data`)
- **Classes**: `PascalCase` (e.g., `DataProcessor`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `API_URL`, `MAX_RETRIES`)
- **Private functions**: Prefix with underscore (e.g., `_helper_function`)

### Error Handling
- Use specific exception types when possible
- Include context in error messages
- Log errors before raising

```python
# Good
if not os.path.isfile(path):
    return [], []
with open(path, "r") as f:
    data = json.load(f)
```

### Streamlit-Specific Guidelines
- Use `st.set_page_config()` at the top of the script
- Use `st.radio()`, `st.selectbox()`, `st.multiselect()` for user input
- Use `st.dataframe()` with `column_config` for structured data display
- Use `st.expander()` for collapsible sections
- Use `st.cache_data()` or `@st.cache_data` decorator for expensive computations

### Data Handling
- Use pandas DataFrames for tabular data
- Use JSON for structured data storage in `data/history/*.json`
- Use YAML for configuration in `players.yaml`
- Handle missing/null values explicitly (check with `pd.isna()` or `None`)

### Multi-Currency Support
When adding currency-related code:
- Support USD, EUR, GBP, and ETH
- Use the existing `CURRENCIES` config in `app.py`
- Store all currencies in history files for flexibility

---

## File Organization

```
sorare_auctions/
├── app.py              # Main dashboard (entry point)
├── fetch_auctions.py  # API data fetcher
├── backfill.py         # Historical data backfill
├── utils.py            # Shared utilities
├── requirements.txt    # Dependencies
├── players.yaml        # Player roster config
├── data/
│   ├── history/        # Player auction history JSONs
│   ├── limited_df.csv  # Out: Defender data
│   ├── limited_gk.csv  # Out: Goalkeeper data
│   └── last_updated.txt
└── docs/               # Documentation
```

---

## Common Tasks

### Adding a New Player
Edit `players.yaml` and add an entry:
```yaml
gk:
  - slug: "player-slug"
    team: "TEAM"
```

### Adding a New Currency
1. Add to `CURRENCIES` dict in `app.py`
2. Update GraphQL query in `fetch_auctions.py`
3. Handle conversion in data loading

### Modifying the Dashboard
- Filters are defined in the tab loop around line 257
- Table columns are configured in `col_config` dict
- Player details section is in the expander around line 338
