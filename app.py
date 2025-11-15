# app.py

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from config import M_TEAMS, V_TEAMS, DB_DEFAULT
from db import (
    get_conn as _get_conn,
    ensure_schema,
    load_seasons,
    get_or_create_season,
    fetch_matches,
    fetch_match_by_id,
    insert_match,
    update_match,
    delete_match,
)

from stats import (
    compute_standings,
    generate_bipartite_schedule,
    schedule_to_df,
    result_points,
    compute_elo_ratings,
)

st.set_page_config(page_title="Stolný hokej – štatistiky", layout="wide")


@st.cache_resource
def get_conn_cached(db_path: str):
    conn = _get_conn(db_path)
    ensure_schema(conn)
    return conn


st.title("Stolný hokej – štatistiky (M doma vs V vonku)")

# --- výber DB + záloha ---
db_path = st.sidebar.text_input("Cesta k databáze (SQLite)", DB_DEFAULT)
conn = get_conn_cached(db_path)

# ZÁLOHA DB – download button v sidebare
try:
    with open(db_path, "rb") as f:
        db_bytes = f.read()
    backup_name = f"hockey_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    st.sidebar.download_button(
        "Stiahnuť zálohu DB",
        data=db_bytes,
        file_name=backup_name,
        mime="application/octet-stream",
    )
except FileNotFoundError:
    st.sidebar.warning("Súbor DB sa nenašiel. Skontroluj cestu k databáze.")

tab = st.sidebar.radio(
    "Sekcia",
    ["Sezóny", "Zadávanie zápasov", "Rozpis", "Prehľad zápasov", "Tabuľky", "Grafy", "Viac sezón", "Head-to-Head"],
)

# --- Sezóny ---
if tab == "Sezóny":
    st.subheader("Sezóny")
    seasons = load_seasons(conn)
    st.dataframe(seasons, use_container_width=True)

    with st.form("add_season"):
        new_label = st.text_input("Názov (napr. 2022/23, Sezóna 4)", value="Sezóna 4")
        submit = st.form_submit_button("Pridať sezónu")
        if submit and new_label.strip():
            sid = get_or_create_season(conn, new_label.strip())
            st.success(f"Sezóna vytvorená / existuje (ID: {sid}).")
            st.rerun()

    st.divider()
    st.subheader("Zmena názvu sezóny")
    seasons = load_seasons(conn)
    if not seasons.empty:
        season_to_edit = st.selectbox(
            "Vyber sezónu na zmenu",
            seasons["label"],
            index=max(0, len(seasons) - 1),
        )
        new_name = st.text_input(
            "Nový názov sezóny", value=season_to_edit, key="edit_season_name"
        )
        if st.button("Uložiť nový názov"):
            try:
                conn.execute(
                    "UPDATE seasons SET label=? WHERE label=?",
                    (new_name.strip(), season_to_edit),
                )
                conn.commit()
                st.success(
                    f"Názov sezóny '{season_to_edit}' bol zmenený na '{new_name}'."
                )
                st.rerun()
            except Exception:
                st.error("Takýto názov už existuje, zvoľ iný.")

# --- Zadávanie zápasov ---
elif tab == "Zadávanie zápasov":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezónu v sekcii 'Sezóny'.")
    else:
        season_label = st.selectbox(
            "Sezóna", seasons["label"], index=max(0, len(seasons) - 1)
        )
        season_id = int(
            seasons.loc[seasons["label"] == season_label, "id"].iloc[0]
        )

        # --- Jednotlivé zadanie zápasu ---
        st.subheader("Jednotlivé zadanie zápasu")

        colA, colB, colC, colD = st.columns(4)
        with colA:
            home = st.selectbox(
                "Domáci (len M tímy)", M_TEAMS, index=0, key="home_sel_M_only"
            )
        with colB:
            away = st.selectbox(
                "Hostia (len V tímy)", V_TEAMS, index=0, key="away_sel_V_only"
            )
        with colC:
            hg = st.number_input("Góly domáci", 0, 99, 0)
            ot = st.checkbox("Po predĺžení?")
        with colD:
            ag = st.number_input("Góly hostia", 0, 99, 0)
            rnd = st.number_input("Kolo", 1, 100, 1)
        is_po = st.checkbox("Play-off zápas", value=False)

        if st.button("Uložiť zápas"):
            round_int = int(rnd)

            # 1) základné kontroly
            if home == away:
                st.error("Domáci a hostia nemôžu byť ten istý tím.")
            elif home not in M_TEAMS:
                st.error("Domáci tím musí byť z M skupiny.")
            elif away not in V_TEAMS:
                st.error("Hosťujúci tím musí byť z V skupiny.")
            elif hg == ag:
                st.error(
                    "Remízy nie sú povolené. Uprav výsledok alebo označ OT a uprav góly."
                )
            else:
                # 2) kontrola, či tím už nehrá v tomto kole
                df_round = fetch_matches(conn, season_id)
                df_round = df_round[df_round["round"] == round_int]

                conflict_home = df_round[
                    (df_round["home_team"] == home)
                    | (df_round["away_team"] == home)
                ]
                conflict_away = df_round[
                    (df_round["home_team"] == away)
                    | (df_round["away_team"] == away)
                ]

                if not conflict_home.empty:
                    st.error(
                        f"V kole {round_int} už hrá tím {home} "
                        f"({conflict_home.iloc[0]['home_team']} {conflict_home.iloc[0]['home_goals']} : "
                        f"{conflict_home.iloc[0]['away_goals']} {conflict_home.iloc[0]['away_team']})."
                    )
                elif not conflict_away.empty:
                    st.error(
                        f"V kole {round_int} už hrá tím {away} "
                        f"({conflict_away.iloc[0]['home_team']} {conflict_away.iloc[0]['home_goals']} : "
                        f"{conflict_away.iloc[0]['away_goals']} {conflict_away.iloc[0]['away_team']})."
                    )
                else:
                    row = {
                        "home_team": home,
                        "away_team": away,
                        "home_goals": int(hg),
                        "away_goals": int(ag),
                        "overtime": 1 if ot else 0,
                        "round": round_int,
                        "season": season_id,
                        "is_playoff": 1 if is_po else 0,
                    }
                    insert_match(conn, row)
                    st.success("Zápas uložený.")

        st.divider()
        st.subheader("Posledné zápasy (aktuálna sezóna)")
        df = fetch_matches(conn, season_id)
        st.dataframe(df.tail(20), use_container_width=True)

        # UNDO POSLEDNÉHO ZÁPASU
        if not df.empty:
            last_match = df.iloc[-1]
            st.markdown("#### Vrátiť posledný zápas (undo)")
            st.write(
                f"Posledný zápis: ID {last_match['id']} – "
                f"{last_match['home_team']} {last_match['home_goals']} : "
                f"{last_match['away_goals']} {last_match['away_team']} "
                f"(kolo {last_match['round']})"
            )
            if st.button("Vymazať posledný zápas v tejto sezóne"):
                delete_match(conn, int(last_match["id"]))
                st.success("Posledný zápas bol vymazaný.")
                st.rerun()

        # --- Zápis výsledkov podľa rozpisu ---
        st.divider()
        st.subheader("Zápis výsledkov podľa rozpisu (celé kolo)")

        all_matches = fetch_matches(conn, season_id)
        if all_matches.empty:
            st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy (rozpis nebol vygenerovaný).")
        else:
            # kolá, kde je aspoň jeden zápas bez výsledku (0:0)
            unplayed = all_matches[
                (all_matches["home_goals"] == 0) & (all_matches["away_goals"] == 0)
            ]
            only_unplayed = False
            if not unplayed.empty:
                only_unplayed = st.checkbox(
                    "Zobraziť len kolá s nevyplnenými výsledkami (0:0)",
                    value=True,
                )

            if only_unplayed and not unplayed.empty:
                rounds_list = sorted(unplayed["round"].unique())
            else:
                rounds_list = sorted(all_matches["round"].unique())

            if not rounds_list:
                st.info("Nie sú žiadne kolá, v ktorých by sa dali doplniť výsledky.")
            else:
                sel_round = st.selectbox(
                    "Kolo podľa rozpisu",
                    rounds_list,
                    index=0,
                )

                df_round = all_matches[all_matches["round"] == int(sel_round)].copy()
                df_round = df_round.sort_values(["id"])

                if df_round.empty:
                    st.info(
                        f"V kole {sel_round} nie sú žiadne zápasy. "
                        f"Najprv vygeneruj a zapíš rozpis v sekcii 'Rozpis'."
                    )
                else:
                    st.markdown(f"**Zápasy v kole {int(sel_round)}:**")

                    # mapovanie id -> pôvodný riadok
                    round_data = {
                        int(row["id"]): row for _, row in df_round.iterrows()
                    }

                    with st.form(f"round_results_form_{season_id}_{sel_round}"):
                        inputs = []
                        for _, row in df_round.iterrows():
                            mid = int(row["id"])
                            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                            with c1:
                                st.write(
                                    f"{row['home_team']} vs {row['away_team']}"
                                )
                            with c2:
                                hg_val = st.number_input(
                                    "Góly dom.",
                                    0,
                                    99,
                                    int(row["home_goals"]),
                                    key=f"hg_round{sel_round}_id{mid}",
                                )
                            with c3:
                                ag_val = st.number_input(
                                    "Góly hosť.",
                                    0,
                                    99,
                                    int(row["away_goals"]),
                                    key=f"ag_round{sel_round}_id{mid}",
                                )
                            with c4:
                                ot_val = st.checkbox(
                                    "OT",
                                    value=bool(row["overtime"]),
                                    key=f"ot_round{sel_round}_id{mid}",
                                )
                            inputs.append((mid, hg_val, ag_val, ot_val))

                        submit_round = st.form_submit_button("Uložiť výsledky kola")

                        if submit_round:
                            any_error = False
                            for mid, hg_val, ag_val, ot_val in inputs:
                                orig = round_data[mid]
                                hg_int = int(hg_val)
                                ag_int = int(ag_val)
                                ot_int = 1 if ot_val else 0

                                # ak sa nič nezmenilo, preskočíme
                                if (
                                    hg_int == int(orig["home_goals"])
                                    and ag_int == int(orig["away_goals"])
                                    and ot_int == int(orig["overtime"])
                                ):
                                    continue

                                if hg_int == ag_int:
                                    st.error(
                                        f"Zápas {orig['home_team']} – {orig['away_team']}: "
                                        f"remíza nie je povolená."
                                    )
                                    any_error = True
                                    continue

                                new_row = orig.to_dict()
                                new_row["home_goals"] = hg_int
                                new_row["away_goals"] = ag_int
                                new_row["overtime"] = ot_int
                                # kolo, sezóna, is_playoff nemeníme

                                update_match(conn, new_row)

                            if not any_error:
                                st.success(
                                    f"Výsledky pre kolo {int(sel_round)} boli uložené."
                                )
                                st.rerun()



# --- Rozpis ---
elif tab == "Rozpis":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezónu v sekcii 'Sezóny'.")
    else:
        season_label = st.selectbox(
            "Sezóna",
            seasons["label"],
            key="sch_season",
            index=max(0, len(seasons) - 1),
        )
        season_id = int(
            seasons.loc[seasons["label"] == season_label, "id"].iloc[0]
        )

        st.write("Generuje sa 32 kôl, v každom 8 zápasov (domáci M, hostia V).")
        if st.button("Vygenerovať rozpis (náhľad)"):
            schedule = generate_bipartite_schedule(32)
            dft = schedule_to_df(schedule, season_id)
            st.session_state["schedule_preview"] = dft
            st.success("Rozpis vygenerovaný – skontroluj nižšie.")

        if "schedule_preview" in st.session_state:
            st.dataframe(
                st.session_state["schedule_preview"],
                use_container_width=True,
                height=500,
            )
            if st.button("Zapísať rozpis do DB (s nulovými výsledkami)"):
                dfp = st.session_state["schedule_preview"]
                cur = conn.cursor()
                added = 0
                for _, r in dfp.iterrows():
                    exists = cur.execute(
                        """
                        SELECT 1 FROM matches
                        WHERE season=? AND round=? AND home_team=? AND away_team=?;
                        """,
                        (
                            int(r["season"]),
                            int(r["round"]),
                            r["home_team"],
                            r["away_team"],
                        ),
                    ).fetchone()
                    if not exists:
                        insert_match(conn, r.to_dict())
                        added += 1
                st.success(f"Zapísaných {added} zápasov.")
                st.rerun()

# --- Prehľad zápasov ---
elif tab == "Prehľad zápasov":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezónu v sekcii 'Sezóny'.")
    else:
        season_label = st.selectbox(
            "Sezóna",
            seasons["label"],
            key="list_season",
            index=max(0, len(seasons) - 1),
        )
        season_id = int(
            seasons.loc[seasons["label"] == season_label, "id"].iloc[0]
        )

        df_all = fetch_matches(conn, season_id)

        if df_all.empty:
            st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy.")
        else:
            st.subheader("Filtrovanie zápasov")

            all_teams = M_TEAMS + V_TEAMS
            team_filter = st.selectbox(
                "Filtrovať podľa tímu (voliteľné)",
                ["(všetky tímy)"] + all_teams,
                index=0,
            )

            type_filter = st.multiselect(
                "Filter typu zápasu (voliteľné)",
                [
                    "Po predĺžení",
                    "Rozdiel 1 gól",
                    "Rozdiel ≥3 góly",
                    "10+ gólov jedného tímu",
                ],
            )

            df = df_all.copy()
            df["home_goals"] = df["home_goals"].astype(int)
            df["away_goals"] = df["away_goals"].astype(int)
            df["diff"] = (df["home_goals"] - df["away_goals"]).abs()
            df["is_ot"] = df["overtime"].astype(bool)
            df["is_one_goal"] = df["diff"] == 1
            df["is_blowout"] = df["diff"] >= 3
            df["is_ten_plus"] = (df["home_goals"] >= 10) | (
                df["away_goals"] >= 10
            )

            # Info stĺpec – krátke značky typu zápasu
            def flags_row(row):
                flags = []
                if row["is_ot"]:
                    flags.append("OT")
                if row["is_one_goal"]:
                    flags.append("1G")
                elif row["is_blowout"]:
                    flags.append("BLOW")
                if row["is_ten_plus"]:
                    flags.append("10+")
                return ", ".join(flags)

            df["Info"] = df.apply(flags_row, axis=1)

            # filter podľa tímu
            if team_filter != "(všetky tímy)":
                df = df[
                    (df["home_team"] == team_filter)
                    | (df["away_team"] == team_filter)
                ]

            # filter podľa typu zápasu
            if type_filter:
                mask = pd.Series(False, index=df.index)
                for t in type_filter:
                    if t == "Po predĺžení":
                        mask |= df["is_ot"]
                    elif t == "Rozdiel 1 gól":
                        mask |= df["is_one_goal"]
                    elif t == "Rozdiel ≥3 góly":
                        mask |= df["is_blowout"]
                    elif t == "10+ gólov jedného tímu":
                        mask |= df["is_ten_plus"]
                df = df[mask]

            st.subheader("Zápasy v sezóne (podľa filtra)")
            st.write(
                f"Zobrazených zápasov: **{len(df)}** z celkových **{len(df_all)}** v sezóne."
            )

            # zobrazíme bez pomocných boolean stĺpcov, necháme Info
            display_cols = [
                c
                for c in df.columns
                if c
                not in [
                    "diff",
                    "is_ot",
                    "is_one_goal",
                    "is_blowout",
                    "is_ten_plus",
                ]
            ]
            st.dataframe(df[display_cols], use_container_width=True, height=480)

            # EXPORT ZÁPASOV DO EXCELU – stále všetky zápasy sezóny
            excel_buf_matches = io.BytesIO()
            with pd.ExcelWriter(excel_buf_matches, engine="xlsxwriter") as writer:
                df_all.to_excel(writer, sheet_name="Zápasy", index=False)
            excel_buf_matches.seek(0)

            st.download_button(
                "Exportovať všetky zápasy sezóny do Excelu",
                data=excel_buf_matches,
                file_name=f"zapasy_{season_label.replace('/', '-')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            st.divider()
            st.subheader("Editácia zápasu")

            if df_all.empty:
                st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy.")
            else:
                edit_id = st.selectbox(
                    "Vyber ID zápasu na editáciu", df_all["id"].tolist()
                )
                match = fetch_match_by_id(conn, int(edit_id))

                if match is None:
                    st.warning("Zápas sa nenašiel.")
                else:
                    with st.form(
                        "edit_match_form", clear_on_submit=False
                    ):
                        cA, cB, cC, cD = st.columns(4)
                        with cA:
                            home = st.selectbox(
                                "Domáci (len M)",
                                M_TEAMS,
                                index=max(
                                    0,
                                    M_TEAMS.index(match["home_team"])
                                    if match["home_team"] in M_TEAMS
                                    else 0,
                                ),
                            )
                            rnd = st.number_input(
                                "Kolo", 1, 100, int(match["round"])
                            )
                        with cB:
                            away = st.selectbox(
                                "Hostia (len V)",
                                V_TEAMS,
                                index=max(
                                    0,
                                    V_TEAMS.index(match["away_team"])
                                    if match["away_team"] in V_TEAMS
                                    else 0,
                                ),
                            )
                            ot = st.checkbox(
                                "Po predĺžení?",
                                value=bool(match["overtime"]),
                            )
                        with cC:
                            hg = st.number_input(
                                "Góly domáci",
                                0,
                                99,
                                int(match["home_goals"]),
                            )
                            is_po = st.checkbox(
                                "Play-off zápas",
                                value=bool(match["is_playoff"]),
                            )
                        with cD:
                            ag = st.number_input(
                                "Góly hostia",
                                0,
                                99,
                                int(match["away_goals"]),
                            )
                            seasons_now = load_seasons(conn)
                            if match["season"] in seasons_now["id"].values:
                                default_idx = int(
                                    seasons_now.index[
                                        seasons_now["id"] == match["season"]
                                    ][0]
                                )
                            else:
                                default_idx = max(
                                    0, len(seasons_now) - 1
                                )
                            season_label_edit = st.selectbox(
                                "Sezóna (pre zápis)",
                                seasons_now["label"],
                                index=default_idx,
                            )
                            season_id_edit = int(
                                seasons_now.loc[
                                    seasons_now["label"]
                                    == season_label_edit,
                                    "id",
                                ].iloc[0]
                            )

                        save = st.form_submit_button("Uložiť zmeny")
                        if save:
                            round_int = int(rnd)

                            if home == away:
                                st.error(
                                    "Domáci a hostia nemôžu byť ten istý tím."
                                )
                            elif home not in M_TEAMS:
                                st.error(
                                    "Domáci tím musí byť z M skupiny."
                                )
                            elif away not in V_TEAMS:
                                st.error(
                                    "Hosťujúci tím musí byť z V skupiny."
                                )
                            elif hg == ag:
                                st.error(
                                    "Remízy nie sú povolené. Uprav góly."
                                )
                            else:
                                # kontrola konfliktov v danom kole + sezóne pri editácii
                                df_round = fetch_matches(
                                    conn, season_id_edit
                                )
                                df_round = df_round[
                                    (df_round["round"] == round_int)
                                    & (df_round["id"] != match["id"])
                                ]

                                conflict_home = df_round[
                                    (df_round["home_team"] == home)
                                    | (df_round["away_team"] == home)
                                ]
                                conflict_away = df_round[
                                    (df_round["home_team"] == away)
                                    | (df_round["away_team"] == away)
                                ]

                                if not conflict_home.empty:
                                    st.error(
                                        f"V kole {round_int} už hrá tím {home} "
                                        f"({conflict_home.iloc[0]['home_team']} {conflict_home.iloc[0]['home_goals']} : "
                                        f"{conflict_home.iloc[0]['away_goals']} {conflict_home.iloc[0]['away_team']})."
                                    )
                                elif not conflict_away.empty:
                                    st.error(
                                        f"V kole {round_int} už hrá tím {away} "
                                        f"({conflict_away.iloc[0]['home_team']} {conflict_away.iloc[0]['home_goals']} : "
                                        f"{conflict_away.iloc[0]['away_goals']} {conflict_away.iloc[0]['away_team']})."
                                    )
                                else:
                                    new_row = {
                                        "id": int(match["id"]),
                                        "home_team": home,
                                        "away_team": away,
                                        "home_goals": int(hg),
                                        "away_goals": int(ag),
                                        "overtime": 1
                                        if ot
                                        else 0,
                                        "round": round_int,
                                        "season": int(season_id_edit),
                                        "is_playoff": 1
                                        if is_po
                                        else 0,
                                    }
                                    update_match(conn, new_row)
                                    st.success(
                                        f"Zápas ID {match['id']} bol aktualizovaný."
                                    )
                                    st.rerun()

            st.divider()
            st.subheader("Hromadné mazanie (voliteľné)")
            sel = st.multiselect(
                "Označ zápasy na vymazanie podľa ID",
                df_all["id"].tolist(),
            )
            if st.button("Vymazať označené"):
                for mid in sel:
                    delete_match(conn, int(mid))
                st.success(f"Vymazané: {len(sel)} záznamov.")
                st.rerun()

# --- Tabuľky ---
elif tab == "Tabuľky":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezónu v sekcii 'Sezóny'.")
    else:
        season_label = st.selectbox(
            "Sezóna",
            seasons["label"],
            key="tbl_season",
            index=max(0, len(seasons) - 1),
        )
        season_id = int(
            seasons.loc[seasons["label"] == season_label, "id"].iloc[0]
        )

        df_matches = fetch_matches(conn, season_id)

        mode = st.radio(
            "Režim",
            ["Klasická tabuľka", "Power ranking (Elo)"],
            horizontal=True,
        )

        # --- KLASICKÁ TABUĽKA ---
        if mode == "Klasická tabuľka":
            scope = st.radio(
                "Zobraziť",
                ["Všetky tímy", "Len M tímy", "Len V tímy"],
                horizontal=True,
            )
            detailed = False
            if scope == "Všetky tímy":
                detailed = st.checkbox("Zobraziť detailné metriky", value=False)

            # pomocná funkcia: súhrny Spolu M / V / ALL
            def build_totals(df_all: pd.DataFrame, detailed_mode: bool) -> pd.DataFrame:
                if "Side" in df_all.columns:
                    df_M = df_all[df_all["Side"] == "M"].copy()
                    df_V = df_all[df_all["Side"] == "V"].copy()
                else:
                    df_M = df_all[df_all["Team"].isin(M_TEAMS)].copy()
                    df_V = df_all[df_all["Team"].isin(V_TEAMS)].copy()

                base_numeric = ["GP", "W", "W-OT", "L-OT", "L", "GF", "GA", "PTS", "GD"]
                extra_counts = (
                    ["1G W", "1G L", "Blowout W", "Blowout L", "SO For", "SO Against", "10+ For", "10+ Against"]
                    if detailed_mode
                    else []
                )

                def make_total_row(sub: pd.DataFrame, label: str, side_val: str | None):
                    row: dict = {}
                    for col in df_all.columns:
                        if pd.api.types.is_numeric_dtype(df_all[col]):
                            row[col] = 0
                        else:
                            row[col] = "-"
                    row["Team"] = label
                    if "Side" in df_all.columns:
                        row["Side"] = side_val if side_val else "-"

                    for c in base_numeric:
                        if c in sub.columns:
                            row[c] = int(sub[c].sum())
                    row["GD"] = row["GF"] - row["GA"]
                    row["P/GP"] = round((row["PTS"] / row["GP"]), 3) if row["GP"] else 0.0

                    if detailed_mode:
                        for c in extra_counts:
                            if c in sub.columns:
                                row[c] = int(sub[c].sum())
                        row["PTS%"] = round(
                            (row["PTS"] / (row["GP"] * 3) * 100), 1
                        ) if row["GP"] else 0.0
                        row["GF/GP"] = round(
                            (row["GF"] / row["GP"]), 3
                        ) if row["GP"] else 0.0
                        row["GA/GP"] = round(
                            (row["GA"] / row["GP"]), 3
                        ) if row["GP"] else 0.0
                        row["AVG GD"] = round(
                            (row["GD"] / row["GP"]), 3
                        ) if row["GP"] else 0.0
                        ot_games = row["W-OT"] + row["L-OT"]
                        row["OT body"] = 2 * row["W-OT"] + 1 * row["L-OT"]
                        row["OT%"] = round(
                            ((ot_games / row["GP"]) * 100), 1
                        ) if row["GP"] else 0.0
                        if "Last5" in row:
                            row["Last5"] = "-"
                        if "Streak" in row:
                            row["Streak"] = "-"

                    return row

                totals_M = make_total_row(df_M, "Spolu M", "M")
                totals_V = make_total_row(df_V, "Spolu V", "V")
                totals_ALL = make_total_row(df_all, "Spolu ALL", None)

                totals_df = pd.DataFrame([totals_M, totals_V, totals_ALL])
                totals_df.index = range(1, len(totals_df) + 1)
                return totals_df

            if scope == "Len M tímy":
                tbl = compute_standings(df_matches, "M")
                st.dataframe(tbl, use_container_width=True)

            elif scope == "Len V tímy":
                tbl = compute_standings(df_matches, "V")
                st.dataframe(tbl, use_container_width=True)

            else:
                table_df = compute_standings(df_matches, "ALL", detailed=detailed)
                table_df.index = range(1, len(table_df) + 1)

                totals_df = build_totals(table_df, detailed_mode=detailed)

                st.dataframe(
                    table_df,
                    use_container_width=True,
                    hide_index=False,
                    column_config={
                        "Team": st.column_config.TextColumn("Team", pinned=True)
                    },
                )

                st.markdown("#### Súhrny – Spolu M / Spolu V / Spolu ALL")
                st.dataframe(totals_df, use_container_width=True)

                # EXPORT DO EXCELU
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    table_df.to_excel(
                        writer, sheet_name="Tabulka", index=True
                    )
                    totals_df.to_excel(
                        writer, sheet_name="Súhrny", index=True
                    )
                excel_buffer.seek(0)

                st.download_button(
                    "Exportovať tabuľku + súhrny do Excelu",
                    data=excel_buffer,
                    file_name=f"standings_{season_label.replace('/', '-')}.xlsx",
                    mime=(
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                )

                # Rekordy + Awards (ako predtým)
                if not df_matches.empty:
                    df_rec = df_matches.copy()
                    df_rec["total_goals"] = df_rec["home_goals"] + df_rec["away_goals"]
                    df_rec["diff_abs"] = (df_rec["home_goals"] - df_rec["away_goals"]).abs()
                    df_rec["max_single"] = df_rec[["home_goals", "away_goals"]].max(axis=1)

                    biggest_win = df_rec.sort_values("diff_abs", ascending=False).iloc[0]
                    most_goals = df_rec.sort_values("total_goals", ascending=False).iloc[0]
                    most_single = df_rec.sort_values("max_single", ascending=False).iloc[0]

                    def fmt_match(row):
                        return (
                            f"{row['home_team']} {row['home_goals']} : "
                            f"{row['away_goals']} {row['away_team']} (kolo {row['round']})"
                        )

                    st.markdown("#### Rekordy sezóny")
                    st.write(f"**Najvyššia výhra (najväčší gólový rozdiel):** {fmt_match(biggest_win)}")
                    st.write(
                        f"**Zápas s najviac gólmi spolu:** {fmt_match(most_goals)} – "
                        f"{int(most_goals['total_goals'])} gólov"
                    )
                    if most_single["home_goals"] >= most_single["away_goals"]:
                        tmax = most_single["home_team"]
                        goals_max = int(most_single["home_goals"])
                    else:
                        tmax = most_single["away_team"]
                        goals_max = int(most_single["away_goals"])
                    st.write(
                        f"**Najviac gólov jedného tímu v zápase:** {tmax} – "
                        f"{goals_max} gólov ({fmt_match(most_single)})"
                    )

                    st.markdown("### Prehľad sezóny – Awards")

                    played = df_matches[
                        (df_matches["home_goals"] != 0)
                        | (df_matches["away_goals"] != 0)
                    ].copy()

                    if played.empty:
                        st.info(
                            "Zatiaľ nie sú odohrané žiadne zápasy v tejto sezóne – prehľad sezóny nie je k dispozícii."
                        )
                    else:
                        total_games = len(played)
                        hg = played["home_goals"].astype(int)
                        ag = played["away_goals"].astype(int)
                        diff = (hg - ag).abs()

                        ot_games = played["overtime"].astype(bool).sum()
                        one_goal = (diff == 1).sum()
                        blowouts = (diff >= 3).sum()
                        ten_plus = ((hg >= 10) | (ag >= 10)).sum()

                        st.markdown(
                            f"- Odohratých zápasov: **{total_games}**  \n"
                            f"- Zápasy po predĺžení: **{ot_games}** ({ot_games / total_games * 100:.1f} %)  \n"
                            f"- Zápasy rozdielom 1 gólu: **{one_goal}** ({one_goal / total_games * 100:.1f} %)  \n"
                            f"- Zápasy rozdielom ≥3 góly: **{blowouts}** ({blowouts / total_games * 100:.1f} %)  \n"
                            f"- Zápasy s aspoň 10 gólmi jedného tímu: **{ten_plus}** ({ten_plus / total_games * 100:.1f} %)"
                        )

                        tbl = table_df.copy()
                        if "Side" in tbl.columns:
                            tbl_M = tbl[tbl["Side"] == "M"].copy()
                            tbl_V = tbl[tbl["Side"] == "V"].copy()
                        else:
                            tbl_M = tbl[tbl["Team"].isin(M_TEAMS)].copy()
                            tbl_V = tbl[tbl["Team"].isin(V_TEAMS)].copy()

                        def pick_best(df_teams: pd.DataFrame):
                            if df_teams.empty:
                                return None
                            return df_teams.sort_values(
                                by=["PTS", "P/GP", "GD", "GF"],
                                ascending=[False, False, False, False],
                            ).iloc[0]

                        best_M = pick_best(tbl_M)
                        best_V = pick_best(tbl_V)

                        if best_M is not None:
                            st.markdown(
                                f"**Najlepší tím Matúša (M):** {best_M['Team']} – "
                                f"{int(best_M['PTS'])} bodov, P/GP {best_M['P/GP']}, "
                                f"skóre {int(best_M['GF'])}:{int(best_M['GA'])} (GD {int(best_M['GD'])})."
                            )

                        if best_V is not None:
                            st.markdown(
                                f"**Najlepší tím Vlada (V):** {best_V['Team']} – "
                                f"{int(best_V['PTS'])} bodov, P/GP {best_V['P/GP']}, "
                                f"skóre {int(best_V['GF'])}:{int(best_V['GA'])} (GD {int(best_V['GD'])})."
                            )

                        best_off = tbl.sort_values(
                            by=["GF", "PTS", "GD"], ascending=[False, False, False]
                        ).iloc[0]
                        best_def = tbl.sort_values(
                            by=["GA", "PTS", "GD"], ascending=[True, False, False]
                        ).iloc[0]

                        st.markdown(
                            f"**Najlepšia ofenzíva:** {best_off['Team']} – "
                            f"{int(best_off['GF'])} strelených gólov."
                        )
                        st.markdown(
                            f"**Najlepšia defenzíva:** {best_def['Team']} – "
                            f"{int(best_def['GA'])} inkasovaných gólov."
                        )

            if scope == "Všetky tímy" and detailed:
                st.markdown(
                    """
                ### Vysvetlivky stĺpcov
                - **Side** – M = tímy Matúša, V = tímy Vlada  
                - **PTS%** – percento získaných bodov z maxima (PTS / (GP*3))  
                - **GF/GP**, **GA/GP** – priemer strelených a inkasovaných gólov na zápas  
                - **AVG GD** – priemerný gólový rozdiel na zápas  
                - **OT%** – percento zápasov, ktoré šli do predĺženia  
                - **OT body** – body získané v predĺžení (2 za výhru, 1 za prehru)  
                - **1G W/L** – výhry/prehry rozdielom 1 gólu  
                - **Blowout W/L** – výhry/prehry rozdielom ≥3 góly  
                - **SO For/Against** – koľkokrát tím udržal nulu / koľkokrát nedal gól  
                - **10+ For/Against** – koľkokrát tím dal / inkasoval ≥10 gólov  
                - **Last5** – posledných 5 výsledkov (W / W-OT / L-OT / L)  
                - **Streak** – aktuálna šnúra (napr. W3, L-OT2)  
                """
                )

        # --- POWER RANKING (ELO) ---
        else:
            if df_matches.empty:
                st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy.")
            else:
                scope_elo = st.radio(
                    "Tímy",
                    ["Všetky tímy", "Len M tímy", "Len V tímy"],
                    horizontal=True,
                )

                standings_all = compute_standings(
                    df_matches, "ALL", detailed=False
                )
                elo_df = compute_elo_ratings(df_matches)

                merged = standings_all.merge(
                    elo_df[["Team", "Rating", "Games"]],
                    on="Team",
                    how="left",
                )

                if scope_elo == "Len M tímy":
                    merged = merged[merged["Side"] == "M"].copy()
                elif scope_elo == "Len V tímy":
                    merged = merged[merged["Side"] == "V"].copy()

                merged = merged.sort_values(
                    by=["Rating", "PTS", "GD", "GF"],
                    ascending=[False, False, False, False],
                ).reset_index(drop=True)
                merged.index = merged.index + 1

                st.dataframe(
                    merged,
                    use_container_width=True,
                    hide_index=False,
                    column_config={
                        "Team": st.column_config.TextColumn("Team", pinned=True)
                    },
                )

                st.markdown(
    """
### Ako funguje Elo (jednoduché vysvetlenie)

- Každý tím začína na **1500 bodoch**.
- Po každom zápase sa rating oboch tímov zmení podľa toho:
  - silný tím získa málo bodov za výhru nad slabým súperom,
  - slabý tím môže získať veľa bodov za výhru nad silným protivníkom.

Používa sa jednoduché pravidlo:

- **Výhra = + body**, **prehra = – body**  
- Koľko bodov? Závisí od toho, ako „ťažký“ bol súper.

Hodnota, o koľko sa Elo pohne, je daná vzorcom:

- **R_new = R_old + K × (S − E)**  
  - **K = 20** (veľkosť zmeny po zápase)  
  - **S = 1** pri výhre, **0** pri prehre  
  - **E = očakávanie výsledku** podľa rozdielu ratingov tímov  

Netreba to počítať ručne — systém to robí automaticky.

---

### Príklad (skutočný pre lepšie pochopenie)

Pred zápasom:

- FIN má rating **1500**
- KAN má rating **1500**

Zápas: **FIN vyhrá nad KAN**

Výsledok:

- FIN dostane približne **+10 bodov**,  
- KAN stratí približne **−10 bodov**.

Nové hodnoty:

- FIN → **1510**  
- KAN → **1490**

Ak by FIN vyhral nad oveľa silnejším súperom (napr. nad tímom s ratingom 1650), dostal by **oveľa viac bodov** (napr. +17),  
a naopak: keď silný tím prehrá so slabým, stratí veľa.

---

### Prečo je Elo užitočné?

- Lepšie ukazuje **skutočnú formu** tímov než tabuľka PTS.  
- Tím môže mať menej bodov ako iný, ale vyšší Elo (porazil silnejších).  
- Reaguje na výsledky priebežne počas sezóny.  
"""
                )


        # --- PREKLIK: zápasy vybraného tímu (bez zásahu do iných tabov) ---
        st.divider()
        st.subheader("Zápasy vybraného tímu v tejto sezóne")

        if df_matches.empty:
            st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy.")
        else:
            all_teams = M_TEAMS + V_TEAMS
            team_sel = st.selectbox(
                "Tím na zobrazenie zápasov",
                all_teams,
                index=0,
                key="tbl_team_matches",
            )

            df_team = df_matches[
                (df_matches["home_team"] == team_sel)
                | (df_matches["away_team"] == team_sel)
            ].copy()

            if df_team.empty:
                st.info("Tento tím zatiaľ v sezóne neodohral žiadny zápas.")
            else:
                df_team["home_goals"] = df_team["home_goals"].astype(int)
                df_team["away_goals"] = df_team["away_goals"].astype(int)
                df_team["diff"] = (df_team["home_goals"] - df_team["away_goals"]).abs()
                df_team["is_ot"] = df_team["overtime"].astype(bool)
                df_team["is_one_goal"] = df_team["diff"] == 1
                df_team["is_blowout"] = df_team["diff"] >= 3
                df_team["is_ten_plus"] = (df_team["home_goals"] >= 10) | (
                    df_team["away_goals"] >= 10
                )

                def flags_row_team(row):
                    flags = []
                    if row["is_ot"]:
                        flags.append("OT")
                    if row["is_one_goal"]:
                        flags.append("1G")
                    elif row["is_blowout"]:
                        flags.append("BLOW")
                    if row["is_ten_plus"]:
                        flags.append("10+")
                    return ", ".join(flags)

                df_team["Info"] = df_team.apply(flags_row_team, axis=1)
                df_team = df_team.sort_values(["round", "id"])

                display_cols_team = [
                    c
                    for c in df_team.columns
                    if c
                    not in [
                        "diff",
                        "is_ot",
                        "is_one_goal",
                        "is_blowout",
                        "is_ten_plus",
                    ]
                ]

                st.dataframe(df_team[display_cols_team], use_container_width=True)

# --- Grafy ---
elif tab == "Grafy":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezónu v sekcii 'Sezóny'.")
    else:
        season_label = st.selectbox(
            "Sezóna",
            seasons["label"],
            key="graph_season",
            index=max(0, len(seasons) - 1),
        )
        season_id = int(
            seasons.loc[seasons["label"] == season_label, "id"].iloc[0]
        )

        df = fetch_matches(conn, season_id)

        mode = st.radio(
            "Režim",
            ["Jeden tím", "Porovnanie viacerých tímov"],
            horizontal=True,
        )

        all_teams = M_TEAMS + V_TEAMS

        # --- Režim: jeden tím (ako doteraz) ---
        if mode == "Jeden tím":
            team = st.selectbox("Tím", all_teams, index=0)

            team_df = df[(df["home_team"] == team) | (df["away_team"] == team)].copy()

            if team_df.empty:
                st.info("Tento tím zatiaľ v sezóne neodohral žiadny zápas.")
            else:
                # zoradíme zápasy podľa kola a ID
                team_df = team_df.sort_values(["round", "id"])

                rows = []
                pts_cum = 0
                games = 0

                for _, m in team_df.iterrows():
                    h = m["home_team"]
                    a = m["away_team"]
                    hg = int(m["home_goals"])
                    ag = int(m["away_goals"])
                    ot = bool(m["overtime"])

                    # z pohľadu vybraného tímu
                    if h == team:
                        gf, ga = hg, ag
                        opp = a
                        pts_team, pts_opp = result_points(hg, ag, ot)
                        pts_this = pts_team
                        ha = "D"  # domáci
                    else:
                        gf, ga = ag, hg
                        opp = h
                        pts_team, pts_opp = result_points(hg, ag, ot)
                        pts_this = pts_opp
                        ha = "V"  # vonku

                    if gf == ga:
                        result_code = "?"
                    elif gf > ga:
                        result_code = "W-OT" if ot else "W"
                    else:
                        result_code = "L-OT" if ot else "L"

                    games += 1
                    pts_cum += pts_this
                    pts_per_game = round(pts_cum / games, 3)

                    rows.append(
                        {
                            "Round": int(m["round"]),
                            "Opponent": opp,
                            "H/V": ha,
                            "GF": gf,
                            "GA": ga,
                            "Result": result_code,
                            "Points": pts_this,
                            "GP": games,
                            "PTS_total": pts_cum,
                            "PTS_per_game": pts_per_game,
                        }
                    )

                prog_df = pd.DataFrame(rows).sort_values("Round")
                prog_df.reset_index(drop=True, inplace=True)

                st.subheader(f"Vývoj bodov – {team}")
                st.dataframe(prog_df, use_container_width=True)

                chart_pts = prog_df.set_index("Round")[["PTS_total"]]
                chart_ppg = prog_df.set_index("Round")[["PTS_per_game"]]

                st.markdown("**Kumulatívne body (PTS_total)**")
                st.line_chart(chart_pts, use_container_width=True)

                st.markdown("**Priemerné body na zápas (PTS_per_game)**")
                st.line_chart(chart_ppg, use_container_width=True)

        # --- Režim: porovnanie viacerých tímov ---
        else:
            teams_sel = st.multiselect(
                "Vyber tímy na porovnanie",
                all_teams,
                default=[M_TEAMS[0], V_TEAMS[0]],
            )

            if len(teams_sel) < 2:
                st.info("Vyber aspoň dva tímy.")
            elif df.empty:
                st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy.")
            else:
                pts_total_by_team = {}
                ppg_by_team = {}

                for team in teams_sel:
                    team_df = df[
                        (df["home_team"] == team) | (df["away_team"] == team)
                    ].copy()
                    if team_df.empty:
                        continue

                    team_df = team_df.sort_values(["round", "id"])

                    pts_cum = 0
                    games = 0
                    rows = []

                    for _, m in team_df.iterrows():
                        h = m["home_team"]
                        a = m["away_team"]
                        hg = int(m["home_goals"])
                        ag = int(m["away_goals"])
                        ot = bool(m["overtime"])

                        if h == team:
                            pts_team, pts_opp = result_points(hg, ag, ot)
                            pts_this = pts_team
                        else:
                            pts_team, pts_opp = result_points(hg, ag, ot)
                            pts_this = pts_opp

                        # remízy by nemali byť, ale pre istotu:
                        if hg == ag:
                            continue

                        games += 1
                        pts_cum += pts_this
                        pts_per_game = pts_cum / games

                        rows.append(
                            {
                                "Round": int(m["round"]),
                                "PTS_total": pts_cum,
                                "PTS_per_game": round(pts_per_game, 3),
                            }
                        )

                    if not rows:
                        continue

                    tdf = pd.DataFrame(rows).drop_duplicates("Round").set_index(
                        "Round"
                    )

                    pts_total_by_team[team] = tdf["PTS_total"]
                    ppg_by_team[team] = tdf["PTS_per_game"]

                if not pts_total_by_team:
                    st.info(
                        "Žiadny z vybraných tímov zatiaľ neodohral zápas v tejto sezóne."
                    )
                else:
                    chart_pts = (
                        pd.DataFrame(pts_total_by_team)
                        .sort_index()
                        .astype(float)
                    )
                    chart_ppg = (
                        pd.DataFrame(ppg_by_team).sort_index().astype(float)
                    )

                    st.subheader("Porovnanie – kumulatívne body (PTS_total)")
                    st.line_chart(chart_pts, use_container_width=True)

                    st.subheader("Porovnanie – priemerné body na zápas (PTS_per_game)")
                    st.line_chart(chart_ppg, use_container_width=True)

                    st.markdown(
                        """
                        - Osa X = kolo (Round)  
                        - Kumulatívne body = suma bodov tímu po jednotlivých kolách  
                        - PTS_per_game = priemerný počet bodov na zápas v danom bode sezóny  
                        """
                    )

# --- Head-to-Head ---
elif tab == "Head-to-Head":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezónu v sekcii 'Sezóny'.")
    else:
        season_label = st.selectbox(
            "Sezóna",
            seasons["label"],
            key="h2h_season",
            index=max(0, len(seasons) - 1),
        )
        season_id = int(
            seasons.loc[seasons["label"] == season_label, "id"].iloc[0]
        )
        df = fetch_matches(conn, season_id)

        # --- Pair head-to-head (single M vs single V) ---
        st.subheader("Head-to-Head dvojíc")

        c1, c2 = st.columns(2)
        with c1:
            t1 = st.selectbox("Tím 1 (M)", M_TEAMS, index=0)
        with c2:
            t2 = st.selectbox("Tím 2 (V)", V_TEAMS, index=0)

        h2h = df[
            ((df.home_team == t1) & (df.away_team == t2))
            | ((df.home_team == t2) & (df.away_team == t1))
        ].copy()
        st.dataframe(h2h, use_container_width=True)

        if not h2h.empty:
            w1 = otw1 = otl1 = 0
            g1 = g2 = 0
            for _, m in h2h.iterrows():
                if m.home_team == t1:
                    g1 += m.home_goals
                    g2 += m.away_goals
                    if m.home_goals > m.away_goals:
                        if m.overtime:
                            otw1 += 1
                        else:
                            w1 += 1
                    else:
                        if m.overtime:
                            otl1 += 1
                elif m.away_team == t1:
                    g1 += m.away_goals
                    g2 += m.home_goals
                    if m.away_goals > m.home_goals:
                        if m.overtime:
                            otw1 += 1
                        else:
                            w1 += 1
                    else:
                        if m.overtime:
                            otl1 += 1
            st.write(
                f"**Súhrn {t1} vs {t2}:** "
                f"Víťazstvá v riadnom čase {w1}, po predĺžení {otw1}; "
                f"prehry po predĺžení {otl1}. "
                f"Góly {t1}:{t2} = {g1}:{g2}."
            )

        st.divider()

        # --- Season M×V matrix ---
        st.subheader("Sezónny matrix M × V")

        if df.empty:
            st.info("V tejto sezóne zatiaľ nie sú žiadne zápasy.")
        else:
            # prepare base structure: for each M-V pair
            matrix = {
                (m, v): {
                    "GP": 0,
                    "PTS_M": 0,
                    "PTS_V": 0,
                    "GF_M": 0,
                    "GA_M": 0,
                }
                for m in M_TEAMS
                for v in V_TEAMS
            }

            # aggregate all matches in season
            for _, mrow in df.iterrows():
                h = mrow["home_team"]
                a = mrow["away_team"]
                hg = int(mrow["home_goals"])
                ag = int(mrow["away_goals"])
                ot = bool(mrow["overtime"])

                # ignore remízy, rovnako ako v standings (nemali by byť)
                if hg == ag:
                    continue

                # match between some M and some V
                if h in M_TEAMS and a in V_TEAMS:
                    m_team = h
                    v_team = a
                    pts_home, pts_away = result_points(hg, ag, ot)
                    key = (m_team, v_team)
                    matrix[key]["GP"] += 1
                    matrix[key]["PTS_M"] += pts_home
                    matrix[key]["PTS_V"] += pts_away
                    matrix[key]["GF_M"] += hg
                    matrix[key]["GA_M"] += ag

                elif h in V_TEAMS and a in M_TEAMS:
                    m_team = a
                    v_team = h
                    pts_home, pts_away = result_points(hg, ag, ot)
                    key = (m_team, v_team)
                    matrix[key]["GP"] += 1
                    # home is V, away is M
                    matrix[key]["PTS_M"] += pts_away
                    matrix[key]["PTS_V"] += pts_home
                    matrix[key]["GF_M"] += ag
                    matrix[key]["GA_M"] += hg

            view_mode = st.radio(
                "Zobraziť v matici",
                ["Body M:V", "Góly M:V"],
                horizontal=True,
            )

            rows_pts = []
            rows_goals = []

            for m_team in M_TEAMS:
                row_pts = {"M\\V": m_team}
                row_goals = {"M\\V": m_team}
                for v_team in V_TEAMS:
                    cell = matrix[(m_team, v_team)]
                    if cell["GP"] == 0:
                        row_pts[v_team] = ""
                        row_goals[v_team] = ""
                    else:
                        row_pts[v_team] = f"{cell['PTS_M']}:{cell['PTS_V']}"
                        row_goals[v_team] = f"{cell['GF_M']}:{cell['GA_M']}"
                rows_pts.append(row_pts)
                rows_goals.append(row_goals)

            df_pts = pd.DataFrame(rows_pts).set_index("M\\V")
            df_goals = pd.DataFrame(rows_goals).set_index("M\\V")

            if view_mode == "Body M:V":
                st.write("**Body M:V za vzájomné zápasy v sezóne**")
                st.dataframe(df_pts, use_container_width=True)
            else:
                st.write("**Góly M:V za vzájomné zápasy v sezóne**")
                st.dataframe(df_goals, use_container_width=True)

            st.markdown(
                """
                - Riadky = M tímy (Matus)  
                - Stĺpce = V tímy (Vlado)  
                - Hodnoty:
                  - pri režime *Body M:V* je to súčet bodov M a V vo vzájomných zápasoch v sezóne
                  - pri režime *Góly M:V* je to súčet gólov M a V vo vzájomných zápasoch v sezóne
                """
            )

# --- Viac sezón ---
elif tab == "Viac sezón":
    seasons = load_seasons(conn)
    if seasons.empty:
        st.info("Najprv vytvor sezóny v sekcii 'Sezóny'.")
    else:
        st.subheader("Vývoj jedného tímu naprieč sezónami")

        seasons_sorted = seasons.sort_values("id")
        all_teams = M_TEAMS + V_TEAMS

        team = st.selectbox("Tím", all_teams, index=0)

        rows_team = []
        for _, srow in seasons_sorted.iterrows():
            sid = int(srow["id"])
            slabel = srow["label"]
            matches_season = fetch_matches(conn, sid)

            if matches_season.empty:
                rows_team.append(
                    {
                        "Sezóna": slabel,
                        "GP": 0,
                        "PTS": 0,
                        "P/GP": 0.0,
                        "GF": 0,
                        "GA": 0,
                        "GD": 0,
                    }
                )
                continue

            standings_all = compute_standings(matches_season, "ALL", detailed=False)
            row_team = standings_all[standings_all["Team"] == team]

            if row_team.empty:
                rows_team.append(
                    {
                        "Sezóna": slabel,
                        "GP": 0,
                        "PTS": 0,
                        "P/GP": 0.0,
                        "GF": 0,
                        "GA": 0,
                        "GD": 0,
                    }
                )
            else:
                r = row_team.iloc[0]
                rows_team.append(
                    {
                        "Sezóna": slabel,
                        "GP": int(r["GP"]),
                        "PTS": int(r["PTS"]),
                        "P/GP": float(r["P/GP"]),
                        "GF": int(r["GF"]),
                        "GA": int(r["GA"]),
                        "GD": int(r["GD"]),
                    }
                )

        df_team_multi = pd.DataFrame(rows_team)

        st.dataframe(df_team_multi, use_container_width=True)

        if not df_team_multi.empty:
            chart_pts = df_team_multi.set_index("Sezóna")[["PTS"]]
            st.markdown("**PTS podľa sezón**")
            st.line_chart(chart_pts, use_container_width=True)

        st.divider()
        st.subheader("Historická tabuľka všetkých tímov (všetky sezóny)")

        # agregované štatistiky cez všetky sezóny
        stats_hist = {
            t: {"Team": t, "GP": 0, "PTS": 0, "GF": 0, "GA": 0}
            for t in all_teams
        }

        for _, srow in seasons_sorted.iterrows():
            sid = int(srow["id"])
            matches_season = fetch_matches(conn, sid)
            if matches_season.empty:
                continue

            standings_all = compute_standings(matches_season, "ALL", detailed=False)
            for _, r in standings_all.iterrows():
                t = r["Team"]
                if t not in stats_hist:
                    continue
                stats_hist[t]["GP"] += int(r["GP"])
                stats_hist[t]["PTS"] += int(r["PTS"])
                stats_hist[t]["GF"] += int(r["GF"])
                stats_hist[t]["GA"] += int(r["GA"])

        hist_rows = []
        for t, vals in stats_hist.items():
            gp = vals["GP"]
            pts = vals["PTS"]
            gf = vals["GF"]
            ga = vals["GA"]
            gd = gf - ga
            ppg = round(pts / gp, 3) if gp > 0 else 0.0
            hist_rows.append(
                {
                    "Team": t,
                    "Side": "M" if t in M_TEAMS else "V",
                    "GP": gp,
                    "PTS": pts,
                    "P/GP": ppg,
                    "GF": gf,
                    "GA": ga,
                    "GD": gd,
                }
            )

        df_hist = pd.DataFrame(hist_rows)
        df_hist = df_hist.sort_values(
            by=["PTS", "P/GP", "GD", "GF"],
            ascending=[False, False, False, False],
        ).reset_index(drop=True)
        df_hist.index = df_hist.index + 1

        st.dataframe(
            df_hist,
            use_container_width=True,
            hide_index=False,
            column_config={
                "Team": st.column_config.TextColumn("Team", pinned=True)
            },
        )

        st.markdown(
            """
            - **Vývoj jedného tímu**: vidíš, ako sa menia štatistiky (GP, PTS, P/GP, GF, GA, GD) medzi sezónami.  
            - **Historická tabuľka**: všetky sezóny dokopy – celkové GP, PTS, P/GP, GF, GA, GD pre každý tím.  
            """
        )

st.caption(
    "M tímy = FIN, SWE, USA, NEM, DAN, FRA, RAK, MAD; "
    "V tímy = KAN, CES, SVK, SUI, LOT, NOR, KAZ, SLO. Body: 3/2/1/0."
)
