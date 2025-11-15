# db.py

import sqlite3
import pandas as pd


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection):
    conn.execute("""CREATE TABLE IF NOT EXISTS seasons (
        id INTEGER PRIMARY KEY,
        label TEXT UNIQUE NOT NULL
    );""")
    conn.execute("""CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_goals INTEGER NOT NULL CHECK(home_goals >= 0),
        away_goals INTEGER NOT NULL CHECK(away_goals >= 0),
        overtime INTEGER NOT NULL CHECK(overtime IN (0,1)),
        round INTEGER NOT NULL CHECK(round >= 1),
        season INTEGER NOT NULL,
        is_playoff INTEGER NOT NULL CHECK(is_playoff IN (0,1)),
        FOREIGN KEY (season) REFERENCES seasons(id) ON UPDATE CASCADE ON DELETE RESTRICT
    );""")
    conn.commit()


def load_seasons(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT id, label FROM seasons ORDER BY id;", conn)


def get_or_create_season(conn: sqlite3.Connection, label: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM seasons WHERE label=?;", (label,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO seasons(label) VALUES(?);", (label,))
    conn.commit()
    return cur.lastrowid


def fetch_matches(conn: sqlite3.Connection, season_id: int | None = None) -> pd.DataFrame:
    q = "SELECT * FROM matches"
    params: list = []
    if season_id:
        q += " WHERE season=?"
        params.append(season_id)
    q += " ORDER BY season, round, id"
    return pd.read_sql_query(q, conn, params=params)


def fetch_match_by_id(conn: sqlite3.Connection, match_id: int) -> dict | None:
    q = "SELECT * FROM matches WHERE id=?"
    df = pd.read_sql_query(q, conn, params=(match_id,))
    return df.iloc[0].to_dict() if not df.empty else None


def insert_match(conn: sqlite3.Connection, row: dict) -> int:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO matches(home_team, away_team, home_goals, away_goals, overtime, round, season, is_playoff)
           VALUES(?,?,?,?,?,?,?,?);""",
        (
            row["home_team"], row["away_team"],
            row["home_goals"], row["away_goals"],
            row["overtime"], row["round"],
            row["season"], row["is_playoff"],
        )
    )
    conn.commit()
    return cur.lastrowid


def update_match(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        UPDATE matches
        SET home_team=?, away_team=?, home_goals=?, away_goals=?,
            overtime=?, round=?, season=?, is_playoff=?
        WHERE id=?
        """,
        (
            row["home_team"], row["away_team"],
            int(row["home_goals"]), int(row["away_goals"]),
            int(row["overtime"]), int(row["round"]),
            int(row["season"]), int(row["is_playoff"]),
            int(row["id"]),
        )
    )
    conn.commit()


def delete_match(conn: sqlite3.Connection, match_id: int) -> None:
    conn.execute("DELETE FROM matches WHERE id=?;", (match_id,))
    conn.commit()
