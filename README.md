# BetRivers Poker Tracker

A local poker hand-history tracking application for **BetRivers Poker**. Parses hand histories, stores data in PostgreSQL, and provides a Streamlit dashboard with session reports, cumulative P&L graphs, and a hand replayer.

**[📖 User Guide — screenshots & feature walkthrough](howto/USER_GUIDE.md)**

---

## Installation

Pre-built installers handle everything automatically — no programming experience required.

**[Download the latest release →](https://github.com/LWashington11/BetRiversTracker/releases)**

### Windows

1. Download **`BetRiversTracker-Setup-<version>.exe`** from the [Releases page](https://github.com/LWashington11/BetRiversTracker/releases).
2. **Right-click the installer and select "Run as administrator"** — PostgreSQL and database setup require admin privileges. Windows will show a User Access Control (UAC) prompt.
3. Follow the wizard (Welcome → License → Location → Install → Done).
4. The wizard will:
   - Download and install **Python 3.13** silently if not already present.
   - Download and install **PostgreSQL 16** silently if not already present.
   - Create a Python virtual environment and install all dependencies.
   - Auto-generate a secure password and write a `.env` configuration file.
   - Initialise the database schema.
5. Launch the app from the **Start Menu** or **Desktop** shortcut.

> **SmartScreen warning?** Click **More info → Run anyway**.
> The installer is not code-signed; Windows displays this warning for unsigned executables.

### macOS

1. Clone or download the repository.
2. In Finder, double-click **`installer/macos/install.command`**.
   - The script detects your CPU architecture (Apple Silicon or Intel) and logs it.
   - Installs **Homebrew** if missing.
   - Installs **Python 3.13** and **PostgreSQL 16** via Homebrew.
   - Copies the app to `~/Applications/BetRiversTracker/`.
   - Creates a virtual environment, installs dependencies, and initialises the database.
   - Places a **`BetRiversTracker.command`** launcher on your Desktop.
3. For daily use, double-click **`BetRiversTracker.command`** on your Desktop.

> **Gatekeeper warning?** Right-click the `.command` file → **Open** → **Open**.
> macOS quarantines scripts downloaded from the internet; this one-time step bypasses the check.
> Alternatively, run this once in Terminal before double-clicking:
> ```
> xattr -cr ~/Downloads/BetRiversTracker/
> ```

> **"Could not be executed because you don't have appropriate privileges"?**
> The file needs execute permission. Run this in Terminal once:
> ```
> chmod +x ~/Downloads/BetRiversTracker/installer/macos/*.command
> ```
> Or: Right-click `install.command` → **Get Info** → Check **Execute** (if visible). Then try again.

---

## Updating

### Windows

1. Download the new **`BetRiversTracker-Setup-<version>.exe`** from the [Releases page](https://github.com/LWashington11/BetRiversTracker/releases).
2. Double-click it and install to the **same location** as before (the default is unchanged).

   The installer will:
   - Overwrite the application files with the new version.
   - Leave your `.env` file, hand history files, and database untouched.
   - Re-create the Python virtual environment and reinstall packages only if needed.

3. Your shortcuts and database remain intact — launch normally when done.

> **If the update fails during pip install:** Uninstall via **Settings → Apps → BetRivers Poker Tracker → Uninstall**, delete the leftover `C:\Users\<you>\AppData\Local\Programs\BetRiversTracker\venv` folder if it exists, then re-run the new installer.

### macOS

1. Download or pull the new version of the repository.
2. In Finder, double-click **`installer/macos/install.command`** again.

   The script will:
   - Update the app files in `~/Applications/BetRiversTracker/`.
   - Re-create the Python virtual environment and reinstall packages.
   - Leave your `.env` file, hand history files, and database untouched.

3. Your Desktop launcher and database remain intact — use them as normal.

> **Tip:** If you cloned the repo with Git, you can `git pull` to fetch the new version before running `install.command` again.

---

## Features

- **Hand History Parser** — Automatically parses BetRivers `.txt` hand histories.
- **PostgreSQL Storage** — Relational schema for hands, players, actions & sessions.
- **Session Report** — Per-day stats: VPIP, PFR, 3Bet, WTSD%, W$SD%, W$WSF, Postflop Agg%, Rake, and more.
- **Cumulative Results Graph** — Net Won over hands played.
- **Hand Replayer** — Visual step-through of any hand.

---

## Tech Stack

| Layer    | Technology              |
|----------|-------------------------|
| Language | Python 3.10+            |
| Database | PostgreSQL + SQLAlchemy |
| Frontend | Streamlit + Plotly      |

---

## Running the App

### Windows

After installation, use the Start Menu or Desktop shortcut.

For the command line (advanced):

| Action | Command |
|--------|---------|
| **Launch dashboard** | `venv\Scripts\streamlit run app\main.py` |
| **Import hand histories** | `venv\Scripts\python -m app.cli import hand_histories\` |
| **Import a single file** | `venv\Scripts\python -m app.cli import hand_histories\sample.txt` |
| **Re-initialise database** | `venv\Scripts\python -m app.cli init` |

### macOS

Double-click **`BetRiversTracker.command`** on your Desktop, or from a terminal:

| Action | Command |
|--------|---------|
| **Launch dashboard** | `./venv/bin/streamlit run app/main.py` |
| **Import hand histories** | `./venv/bin/python -m app.cli import hand_histories/` |
| **Import a single file** | `./venv/bin/python -m app.cli import hand_histories/sample.txt` |
| **Re-initialise database** | `./venv/bin/python -m app.cli init` |

The dashboard opens at **http://localhost:8501** in your default browser.

---

## Configuration

The installer creates a `.env` file in the project root. Edit it to change any settings:

```env
# PostgreSQL connection settings
PGUSER=postgres
PGPASSWORD=<auto-generated>
PGHOST=localhost
PGPORT=5432
PGDATABASE=betrivers_tracker

# Hand history directory
HAND_HISTORY_DIR=./hand_histories
```

---

## Manual Setup (Advanced)

If you prefer to set things up without the installer:

### 1. Create the database

```sql
CREATE DATABASE betrivers_tracker;
```

Or run the full DDL:

```bash
psql -U postgres -f schema.sql
```

### 2. Install dependencies

```bash
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure

Create a `.env` file in the project root with your PostgreSQL credentials (see Configuration section above).

### 4. Initialise tables

```bash
python -m app.cli init
```

### 5. Import hand histories

```bash
# Single file
python -m app.cli import hand_histories/sample.txt

# Whole directory
python -m app.cli import hand_histories/
```

### 6. Launch the dashboard

```bash
streamlit run app/main.py
```

Open **http://localhost:8501** in your browser.

---

## Session Report Columns

| Column | Description |
|--------|-------------|
| VPIP   | Voluntarily Put money In Pot % |
| PFR    | Pre-Flop Raise % |
| 3Bet   | 3-Bet % |
| WTSD%  | Went to Showdown % |
| W$SD%  | Won $ at Showdown % |
| W$WSF  | Won $ When Saw Flop % |
| Agg%   | Postflop Aggression % |
| Rake   | Rake paid (in $) |

---

## Testing

```bash
pytest tests/ -v
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| SmartScreen blocks the installer | Click **More info → Run anyway** |
| Gatekeeper blocks install.command | Right-click → **Open** → **Open** |
| `could not connect to server` | Ensure the PostgreSQL service is running |
| `database "betrivers_tracker" does not exist` | Run `python -m app.cli init` from the install directory |
| Port 8501 already in use | Close other Streamlit instances, or add `--server.port 8502` to the launch command |

---

## Project Structure

```
BetRiversTracker/
├── installer/
│   ├── windows/
│   │   ├── setup.iss          # Inno Setup script (builds the .exe wizard)
│   │   ├── launcher.vbs       # Windows daily launcher (Start Menu / Desktop)
│   │   └── build.bat          # Developer build helper
│   └── macos/
│       ├── install.command    # macOS one-shot installer (double-click)
│       └── launch.command     # macOS daily launcher (Desktop shortcut)
├── .github/workflows/
│   └── release.yml            # Auto-build & publish .exe on v* tag push
├── app/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                 # CLI for init & import
│   ├── config.py              # DB connection, paths
│   ├── constants.py           # Shared constants (versions, URLs, mappings)
│   ├── main.py                # Streamlit multi-page entry point
│   ├── dashboard.py           # Dashboard page
│   ├── hands_report.py        # Hands report page
│   ├── replayer.py            # Hand replayer page
│   ├── importer.py            # Parsed dict → PostgreSQL
│   ├── models.py              # SQLAlchemy ORM models
│   └── parser.py              # BetRivers hand-history regex parser
├── hand_histories/
│   └── sample.txt
├── tests/
│   └── test_parser.py
├── schema.sql                 # Raw DDL (alternative to SQLAlchemy create)
├── requirements.txt
└── README.md
```
