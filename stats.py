# stats.py

import pandas as pd
from config import M_TEAMS, V_TEAMS


def result_points(home_g: int, away_g: int, ot: bool) -> tuple[int, int]:
    if home_g > away_g:
        return (2 if ot else 3, 1 if ot else 0)
    elif away_g > home_g:
        return (1 if ot else 0, 2 if ot else 3)
    else:
        return (0, 0)  # remízy nepovoľujeme; validátor ich blokuje


def _result_code_for_team(hg: int, ag: int, ot: bool, is_home: bool) -> str:
    """
    Vráti kód výsledku z pohľadu tímu: 'W', 'W-OT', 'L-OT', 'L'
    """
    win = (hg > ag) if is_home else (ag > hg)
    if win:
        return "W-OT" if ot else "W"
    else:
        return "L-OT" if ot else "L"


def compute_standings(matches: pd.DataFrame, scope: str = "ALL", detailed: bool = False) -> pd.DataFrame:
    """
    scope: "ALL" | "M" | "V"
    detailed: ak True a scope == "ALL", zobrazia sa rozšírené metriky:
      PTS%, GF/GP, GA/GP, AVG GD, OT%, OT body,
      1G/Blowout/SHO, Last5, Streak, 10+ For/Against
    """
    # Tímy podľa rozsahu
    if scope == "M":
        teams = M_TEAMS
    elif scope == "V":
        teams = V_TEAMS
    else:
        teams = M_TEAMS + V_TEAMS  # ALL

    data: dict[str, dict] = {
        t: {
            "Team": t, "GP": 0, "W": 0, "W-OT": 0, "L-OT": 0, "L": 0,
            "GF": 0, "GA": 0, "PTS": 0,
            "_1G_W": 0, "_1G_L": 0,
            "_BLOW_W": 0, "_BLOW_L": 0,
            "_SO_FOR": 0, "_SO_AGAINST": 0,
            "_OT_GAMES": 0,
            "_TENPLUS_FOR": 0, "_TENPLUS_AGAINST": 0,
        } for t in teams
    }
    # na formu – chronologický zoznam výsledkov
    form_seq: dict[str, list[str]] = {t: [] for t in teams}

    if not matches.empty:
        matches_sorted = matches.sort_values(["round", "id"])
        for _, m in matches_sorted.iterrows():
            h, a = m["home_team"], m["away_team"]
            hg, ag = int(m["home_goals"]), int(m["away_goals"])
            ot = bool(m["overtime"])

            if hg == ag:
                continue  # remízy ignorujeme

            # domáci
            if h in data:
                d = data[h]
                d["GP"] += 1
                d["GF"] += hg
                d["GA"] += ag

                # W / L / W-OT / L-OT
                if hg > ag:
                    if ot:
                        d["W-OT"] += 1
                    else:
                        d["W"] += 1
                else:
                    if ot:
                        d["L-OT"] += 1
                    else:
                        d["L"] += 1

                # shutouts
                if ag == 0:
                    d["_SO_FOR"] += 1
                if hg == 0:
                    d["_SO_AGAINST"] += 1

                # one-goal / blowout
                diff = abs(hg - ag)
                if diff == 1:
                    if hg > ag:
                        d["_1G_W"] += 1
                    else:
                        d["_1G_L"] += 1
                elif diff >= 3:
                    if hg > ag:
                        d["_BLOW_W"] += 1
                    else:
                        d["_BLOW_L"] += 1

                # 10+ góly
                if hg >= 10:
                    d["_TENPLUS_FOR"] += 1
                if ag >= 10:
                    d["_TENPLUS_AGAINST"] += 1

                # OT zápas
                if ot:
                    d["_OT_GAMES"] += 1

                # body
                d["PTS"] += (2 if ot else 3) if hg > ag else (1 if ot else 0)

                # forma
                form_seq[h].append(_result_code_for_team(hg, ag, ot, True))

            # hostia
            if a in data:
                d = data[a]
                d["GP"] += 1
                d["GF"] += ag
                d["GA"] += hg

                # W / L / W-OT / L-OT
                if ag > hg:
                    if ot:
                        d["W-OT"] += 1
                    else:
                        d["W"] += 1
                else:
                    if ot:
                        d["L-OT"] += 1
                    else:
                        d["L"] += 1

                # shutouts
                if hg == 0:
                    d["_SO_FOR"] += 1
                if ag == 0:
                    d["_SO_AGAINST"] += 1

                # one-goal / blowout
                diff = abs(hg - ag)
                if diff == 1:
                    if ag > hg:
                        d["_1G_W"] += 1
                    else:
                        d["_1G_L"] += 1
                elif diff >= 3:
                    if ag > hg:
                        d["_BLOW_W"] += 1
                    else:
                        d["_BLOW_L"] += 1

                # 10+ góly
                if ag >= 10:
                    d["_TENPLUS_FOR"] += 1
                if hg >= 10:
                    d["_TENPLUS_AGAINST"] += 1

                # OT zápas
                if ot:
                    d["_OT_GAMES"] += 1

                # body
                d["PTS"] += (2 if ot else 3) if ag > hg else (1 if ot else 0)

                # forma
                form_seq[a].append(_result_code_for_team(hg, ag, ot, False))

    df = pd.DataFrame([data[t] for t in teams])
    df["GD"] = df["GF"] - df["GA"]

    def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
        s = num / den
        return s.replace([float("inf"), float("-inf")], 0).fillna(0)

    df["P/GP"] = safe_div(df["PTS"], df["GP"]).round(3)

    if scope == "ALL" and detailed:
        df["Side"] = df["Team"].apply(lambda x: "M" if x in M_TEAMS else "V")
        df["PTS%"] = (safe_div(df["PTS"], df["GP"] * 3) * 100).round(1)
        df["GF/GP"] = safe_div(df["GF"], df["GP"]).round(3)
        df["GA/GP"] = safe_div(df["GA"], df["GP"]).round(3)
        df["AVG GD"] = safe_div(df["GD"], df["GP"]).round(3)
        df["OT%"] = (safe_div(df["_OT_GAMES"], df["GP"]) * 100).round(1)
        df["OT body"] = (2 * df["W-OT"] + 1 * df["L-OT"]).astype(int)
        df["1G W"] = df["_1G_W"].astype(int)
        df["1G L"] = df["_1G_L"].astype(int)
        df["Blowout W"] = df["_BLOW_W"].astype(int)
        df["Blowout L"] = df["_BLOW_L"].astype(int)
        df["SO For"] = df["_SO_FOR"].astype(int)
        df["SO Against"] = df["_SO_AGAINST"].astype(int)
        df["10+ For"] = df["_TENPLUS_FOR"].astype(int)
        df["10+ Against"] = df["_TENPLUS_AGAINST"].astype(int)

        def last5_str(seq: list[str]) -> str:
            if not seq:
                return ""
            return ", ".join(seq[-5:])

        def streak_str(seq: list[str]) -> str:
            if not seq:
                return ""
            last = seq[-1]
            n = 0
            for r in reversed(seq):
                if r == last:
                    n += 1
                else:
                    break
            return f"{last}{n}"

        df["Last5"] = df["Team"].map(lambda t: last5_str(form_seq.get(t, [])))
        df["Streak"] = df["Team"].map(lambda t: streak_str(form_seq.get(t, [])))

        order = [
            "Team", "Side", "GP", "W", "W-OT", "L-OT", "L", "GF", "GA", "GD", "P/GP", "PTS", "PTS%",
            "GF/GP", "GA/GP", "AVG GD", "OT%", "OT body",
            "1G W", "1G L", "Blowout W", "Blowout L",
            "SO For", "SO Against", "10+ For", "10+ Against",
            "Last5", "Streak",
        ]
        df = df[order]

    elif scope == "ALL":
        df["Side"] = df["Team"].apply(lambda x: "M" if x in M_TEAMS else "V")
        order = ["Team", "Side", "GP", "W", "W-OT", "L-OT", "L", "GF", "GA", "GD", "PTS", "P/GP"]
        df = df[order]
    else:
        order = ["Team", "GP", "W", "W-OT", "L-OT", "L", "GF", "GA", "GD", "PTS", "P/GP"]
        df = df[order]

    df = df.sort_values(
        by=["PTS", "W", "W-OT", "GD", "GF"],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)
    df.index = df.index + 1
    return df


def generate_bipartite_schedule(rounds: int = 32):
    """
    M tímy sú stále v poradí:
      FIN, SWE, USA, NEM, DAN, FRA, RAK, MAD

    V tímy v 1. kole:
      SLO, KAZ, NOR, LOT, SVK, SUI, CES, KAN

    V ďalších kolách sa V tímy posúvajú „hore“ o 1:
      nový zoznam = [2., 3., ..., 8., 1.]
    """
    M = M_TEAMS.copy()
    v_start = ["SLO", "KAZ", "NOR", "LOT", "SVK", "SUI", "CES", "KAN"]
    # voliteľná kontrola, že je to len rotácia V_TEAMS
    # assert set(v_start) == set(V_TEAMS)

    V = v_start.copy()
    schedule: list[list[tuple[str, str]]] = []

    for _ in range(rounds):
        pairings = []
        for i in range(8):
            pairings.append((M[i], V[i]))
        schedule.append(pairings)
        # posun „hore“ – prvý ide na koniec
        V = V[1:] + V[:1]

    return schedule


def schedule_to_df(schedule, season_id: int) -> pd.DataFrame:
    rows = []
    for rnd, pairings in enumerate(schedule, start=1):
        for home, away in pairings:
            rows.append(
                {
                    "home_team": home,
                    "away_team": away,
                    "home_goals": 0,
                    "away_goals": 0,
                    "overtime": 0,
                    "round": rnd,
                    "season": season_id,
                    "is_playoff": 0,
                }
            )
    return pd.DataFrame(rows)

def compute_elo_ratings(matches: pd.DataFrame, base_rating: float = 1500.0, k: float = 20.0) -> pd.DataFrame:
    """
    Vypočíta Elo ratingy pre všetky tímy na základe odohraných zápasov v sezóne.
    Ignoruje:
      - zápasy 0:0 (rozpis bez odohraného výsledku)
      - remízy (nemali by existovať, ale pre istotu)
    Výstup: DataFrame s Team, Side (M/V), Rating, Games
    """
    all_teams = list(dict.fromkeys(M_TEAMS + V_TEAMS))  # zachová poradie
    ratings = {t: float(base_rating) for t in all_teams}
    games = {t: 0 for t in all_teams}

    if matches.empty:
        rows = []
        for t in all_teams:
            rows.append(
                {
                    "Team": t,
                    "Side": "M" if t in M_TEAMS else "V",
                    "Rating": ratings[t],
                    "Games": games[t],
                }
            )
        return pd.DataFrame(rows)

    m = matches.copy()
    m["home_goals"] = m["home_goals"].astype(int)
    m["away_goals"] = m["away_goals"].astype(int)

    # len odohrané zápasy (nie čisté 0:0 z rozpisu)
    played = m[(m["home_goals"] != 0) | (m["away_goals"] != 0)].copy()
    # pre istotu ignorujeme remízy
    played = played[played["home_goals"] != played["away_goals"]]

    if played.empty:
        rows = []
        for t in all_teams:
            rows.append(
                {
                    "Team": t,
                    "Side": "M" if t in M_TEAMS else "V",
                    "Rating": ratings[t],
                    "Games": games[t],
                }
            )
        return pd.DataFrame(rows)

    sort_cols = ["round"]
    if "id" in played.columns:
        sort_cols.append("id")
    played = played.sort_values(sort_cols)

    for _, row in played.iterrows():
        h = row["home_team"]
        a = row["away_team"]
        hg = int(row["home_goals"])
        ag = int(row["away_goals"])

        if h not in ratings or a not in ratings:
            continue

        Rh = ratings[h]
        Ra = ratings[a]

        # výsledok z pohľadu domácich (Matúšove tímy sú vždy doma, ale Elo to nerieši špeciálne)
        if hg > ag:
            Sh = 1.0
            Sa = 0.0
        else:
            Sh = 0.0
            Sa = 1.0

        Eh = 1.0 / (1.0 + 10 ** ((Ra - Rh) / 400.0))
        Ea = 1.0 - Eh

        Rh_new = Rh + k * (Sh - Eh)
        Ra_new = Ra + k * (Sa - Ea)

        ratings[h] = Rh_new
        ratings[a] = Ra_new
        games[h] += 1
        games[a] += 1

    rows = []
    for t in all_teams:
        rows.append(
            {
                "Team": t,
                "Side": "M" if t in M_TEAMS else "V",
                "Rating": ratings[t],
                "Games": games[t],
            }
        )
    df = pd.DataFrame(rows)
    df["Rating"] = df["Rating"].round(1)
    return df
