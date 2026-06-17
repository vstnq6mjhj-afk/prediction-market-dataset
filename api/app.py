import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

st_autorefresh(interval=60_000, key="dashboard_refresh")

DB_PATH = "data/warehouse.duckdb"

st.set_page_config(
    page_title="Prediction Market Dataset",
    layout="wide",
)

st.title("Prediction Market Dataset")
st.caption("Live cross-platform prediction market data warehouse")

conn = duckdb.connect(DB_PATH, read_only=True)

page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Markets", "Platforms", "Movers", "Market Detail"],
)

platforms_df = conn.execute("""
    SELECT DISTINCT platform
    FROM market_snapshots
    WHERE platform IS NOT NULL
    ORDER BY platform
""").df()

platforms = ["All"] + platforms_df["platform"].tolist()
selected_platform = st.sidebar.selectbox("Platform", platforms)
search = st.sidebar.text_input("Search Markets")

filters = []
params = []

if selected_platform != "All":
    filters.append("platform = ?")
    params.append(selected_platform)

if search:
    filters.append("LOWER(title) LIKE ?")
    params.append(f"%{search.lower()}%")

where_clause = ""
if filters:
    where_clause = "WHERE " + " AND ".join(filters)

latest_time_sql = """
    SELECT MAX(snapshot_time)
    FROM market_snapshots
"""

latest_time = conn.execute(latest_time_sql).fetchone()[0]


def sql_df(query, query_params=None):
    if query_params is None:
        query_params = []
    return conn.execute(query, query_params).df()


def clean_display(df):
    if df.empty:
        return df
    return df.replace({pd.NA: None, float("inf"): None, float("-inf"): None})


if page == "Dashboard":
    total_rows = conn.execute(f"""
        SELECT COUNT(*)
        FROM market_snapshots
        {where_clause}
    """, params).fetchone()[0]

    unique_markets = conn.execute(f"""
        SELECT COUNT(DISTINCT platform || ':' || market_id)
        FROM market_snapshots
        {where_clause}
    """, params).fetchone()[0]

    snapshots = conn.execute(f"""
        SELECT COUNT(DISTINCT snapshot_time)
        FROM market_snapshots
        {where_clause}
    """, params).fetchone()[0]

    latest_snapshot = conn.execute(f"""
        SELECT MAX(snapshot_time)
        FROM market_snapshots
        {where_clause}
    """, params).fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Rows", f"{total_rows:,}")
    c2.metric("Unique Markets", f"{unique_markets:,}")
    c3.metric("Snapshots", f"{snapshots:,}")
    c4.metric("Latest Snapshot", str(latest_snapshot)[:19])

    st.divider()

    st.subheader("Platform Coverage")

    platform_stats = sql_df(f"""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT platform || ':' || market_id) AS unique_markets,
            ROUND(AVG(NULLIF(volume, 0)), 4) AS avg_volume,
            ROUND(AVG(NULLIF(liquidity, 0)), 4) AS avg_liquidity
        FROM market_snapshots
        {where_clause}
        GROUP BY platform
        ORDER BY rows DESC
    """, params)

    st.dataframe(clean_display(platform_stats), use_container_width=True)

    st.subheader("Snapshot Growth")

    growth = sql_df(f"""
        SELECT
            snapshot_time,
            COUNT(*) AS rows
        FROM market_snapshots
        {where_clause}
        GROUP BY snapshot_time
        ORDER BY snapshot_time
    """, params)

    if not growth.empty:
        growth["snapshot_time"] = pd.to_datetime(growth["snapshot_time"])
        fig = px.line(
            growth,
            x="snapshot_time",
            y="rows",
            markers=True,
            title="Rows Collected Over Time",
        )
        st.plotly_chart(fig, use_container_width=True)


elif page == "Markets":
    st.subheader("Latest Top Volume Markets")

    top_volume = sql_df(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time = (
                SELECT MAX(snapshot_time)
                FROM market_snapshots
            )
        )
        SELECT
            platform,
            market_id,
            title,
            yes_price,
            no_price,
            volume,
            liquidity,
            snapshot_time
        FROM latest
        WHERE 1=1
        {("AND " + " AND ".join(filters)) if filters else ""}
        ORDER BY volume DESC NULLS LAST
        LIMIT 100
    """, params)

    st.dataframe(clean_display(top_volume), use_container_width=True)

    st.subheader("Latest Snapshot Explorer")

    latest_snapshot = sql_df(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time = (
                SELECT MAX(snapshot_time)
                FROM market_snapshots
            )
        )
        SELECT *
        FROM latest
        WHERE 1=1
        {("AND " + " AND ".join(filters)) if filters else ""}
        ORDER BY platform, volume DESC NULLS LAST
        LIMIT 300
    """, params)

    st.dataframe(clean_display(latest_snapshot), use_container_width=True)


elif page == "Platforms":
    st.subheader("Platform Comparison")

    platform_stats = sql_df(f"""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT platform || ':' || market_id) AS unique_markets,
            ROUND(AVG(NULLIF(volume, 0)), 4) AS avg_volume,
            ROUND(AVG(NULLIF(liquidity, 0)), 4) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        {where_clause}
        GROUP BY platform
        ORDER BY rows DESC
    """, params)

    st.dataframe(clean_display(platform_stats), use_container_width=True)

    if not platform_stats.empty:
        fig_rows = px.bar(
            platform_stats,
            x="platform",
            y="rows",
            title="Rows by Platform",
        )
        st.plotly_chart(fig_rows, use_container_width=True)

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets by Platform",
        )
        st.plotly_chart(fig_unique, use_container_width=True)


elif page == "Movers":
    st.subheader("Biggest Price Movers")

    movers = sql_df(f"""
        SELECT
            platform,
            market_id,
            title,
            MIN(yes_price) AS low_price,
            MAX(yes_price) AS high_price,
            MAX(yes_price) - MIN(yes_price) AS price_move,
            COUNT(*) AS snapshots
        FROM market_snapshots
        {where_clause}
        GROUP BY platform, market_id, title
        HAVING COUNT(*) > 3
        ORDER BY price_move DESC NULLS LAST
        LIMIT 100
    """, params)

    st.dataframe(clean_display(movers), use_container_width=True)

    if not movers.empty:
        fig = px.bar(
            movers.head(25),
            x="price_move",
            y="title",
            orientation="h",
            title="Top Price Movers",
        )
        st.plotly_chart(fig, use_container_width=True)


elif page == "Market Detail":
    st.subheader("Market Detail")

    market_options = sql_df(f"""
        SELECT
            platform,
            market_id,
            MAX(title) AS title,
            MAX(snapshot_time) AS latest_snapshot,
            COUNT(*) AS observations
        FROM market_snapshots
        {where_clause}
        GROUP BY platform, market_id
        ORDER BY latest_snapshot DESC, observations DESC
        LIMIT 1000
    """, params)

    if market_options.empty:
        st.warning("No markets found.")
    else:
        market_options["label"] = (
            market_options["platform"].astype(str)
            + " | "
            + market_options["title"].astype(str)
            + " | "
            + market_options["market_id"].astype(str)
        )

        selected_label = st.selectbox(
            "Select a market",
            market_options["label"].tolist(),
        )

        selected_row = market_options[market_options["label"] == selected_label].iloc[0]
        selected_platform_detail = selected_row["platform"]
        selected_market_id = selected_row["market_id"]

        detail = sql_df("""
            SELECT *
            FROM market_snapshots
            WHERE platform = ?
              AND market_id = ?
            ORDER BY snapshot_time
        """, [selected_platform_detail, selected_market_id])

        st.dataframe(clean_display(detail.tail(100)), use_container_width=True)

        latest = detail.tail(1).iloc[0]

        c1, c2, c3 = st.columns(3)
        c1.metric("Latest YES", latest.get("yes_price"))
        c2.metric("Latest Volume", latest.get("volume"))
        c3.metric("Observations", len(detail))

        detail["snapshot_time"] = pd.to_datetime(detail["snapshot_time"])

        st.subheader("YES Price History")
        fig_price = px.line(
            detail,
            x="snapshot_time",
            y="yes_price",
            markers=True,
        )
        st.plotly_chart(fig_price, use_container_width=True)

        st.subheader("Volume History")
        fig_volume = px.bar(
            detail,
            x="snapshot_time",
            y="volume",
        )
        st.plotly_chart(fig_volume, use_container_width=True)

        st.subheader("Liquidity History")
        fig_liquidity = px.line(
            detail,
            x="snapshot_time",
            y="liquidity",
            markers=True,
        )
        st.plotly_chart(fig_liquidity, use_container_width=True)

conn.close()