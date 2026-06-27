import re
import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px
import os
import stripe
from dotenv import load_dotenv
from supabase import create_client
from difflib import SequenceMatcher
from streamlit_autorefresh import st_autorefresh

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
params = st.query_params

if params.get("payment") == "success":
    paid_email = params.get("email")

    if isinstance(paid_email, list):
        paid_email = paid_email[0]

    if paid_email:
        paid_email = paid_email.strip().lower()

        supabase.table("user_subscriptions").upsert({
            "email": paid_email,
            "status": "active",
        }).execute()

        st.session_state.user_email = paid_email
        st.success("Subscription activated. You now have Pro access.")
    else:
        st.error("Payment succeeded, but no email was returned.")

def extract_office(title):
    title = str(title).lower()

    if "vice president" in title or "vice presidential" in title:
        return "vice_president"

    if "president" in title or "presidential" in title:
        return "president"

    return None


if "user_email" not in st.session_state:
    st.session_state.user_email = None

def extract_year(title):
    match = re.search(r"\b(20[2-9][0-9])\b", str(title))
    return match.group(1) if match else None


def extract_party(title):
    title = str(title).lower()

    if "democratic" in title or "democrat" in title:
        return "democratic"

    if "republican" in title or "gop" in title:
        return "republican"

    return None


def extract_event_type(title):
    title = str(title).lower()

    if "nomination" in title or "nominee" in title:
        return "nomination"

    if "president" in title or "election" in title:
        return "election"

    if "world cup" in title or "fifa" in title:
        return "world_cup"

    if "bitcoin" in title or "btc" in title:
        return "bitcoin"

    return "other"


def extract_candidate(title):
    title = str(title).lower()

    candidates = [
        "gavin newsom",
        "kamala harris",
        "pete buttigieg",
        "andy beshear",
        "jon ossoff",
        "mark kelly",
        "rahm emanuel",
        "wes moore",
        "gretchen whitmer",
        "josh shapiro",
        "donald trump",
        "joe biden",
        "ron desantis",
        "j d vance",
        "jd vance",
    ]

    for candidate in candidates:
        if candidate in title:
            return candidate.replace("j d", "jd")

    return None


def canonical_key(title):
    return {
        "year": extract_year(title),
        "party": extract_party(title),
        "event_type": extract_event_type(title),
        "candidate": extract_candidate(title),
    }

def similarity(a, b):
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()


def normalize_title(title):
    if not title:
        return ""

    title = str(title).lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)

    stopwords = {
        "will", "yes", "the", "a", "an", "in", "on", "for", "of", "to",
        "with", "and", "or", "who", "what", "when", "win", "wins" "who",
        "will", "wins", "win", "winner", "market", "nomination", "presidential",
        "democratic", "democratic", "republican", "election", "2024", "2025", "2026",
        "2027", "2028",

    }

    words = []
    for w in title.split():
        if w not in stopwords and len(w) > 2:
            words.append(w)

    return " ".join(words[:7])


st_autorefresh(interval=60_000, key="dashboard_refresh")

DB_PATH = "data/warehouse.duckdb"

st.set_page_config(
    page_title="Prediction Market Dataset",
    layout="wide",
)

st.title("Prediction Market Dataset")
st.caption("Live cross-platform prediction market data warehouse")

with st.sidebar:
    st.subheader("Account")

    if st.session_state.get("user_email"):

        user_email = st.session_state.user_email.strip().lower()

        try:
            subscription = (
                supabase.table("user_subscriptions")
                .select("status")
                .eq("email", user_email)
                .execute()
            )

            is_pro = (
                len(subscription.data) > 0
                and subscription.data[0].get("status") == "active"
            )

        except Exception:
            is_pro = False

        st.success(user_email)

        if is_pro:
            st.success("Subscription: Pro")
        else:
            st.info("Subscription: Free")

            if st.button("Upgrade to Pro"):
                checkout_session = stripe.checkout.Session.create(
                    mode="subscription",
                    payment_method_types=["card"],
                    line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
                    customer_email=user_email,
                    success_url=f"http://localhost:8501/?payment=success&email={user_email}",
                    cancel_url="http://localhost:8501",
                )

                st.link_button(
                    "Open Stripe Checkout",
                    checkout_session.url
                )

        if st.button("Log out"):
            st.session_state.user_email = None
            st.session_state.session = None
            st.rerun()

    else:
        auth_mode = st.radio(
            "Account",
            ["Log in", "Sign up"],
            horizontal=True,
        )

        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if auth_mode == "Sign up":
            if st.button("Create account"):
                try:
                    supabase.auth.sign_up({
                        "email": email.strip().lower(),
                        "password": password,
                    })
                    st.success("Account created. You can now log in.")
                except Exception as e:
                    st.error(f"Signup failed: {e}")

        if auth_mode == "Log in":
            if st.button("Log in"):
                try:
                    result = supabase.auth.sign_in_with_password({
                        "email": email.strip().lower(),
                        "password": password,
                    })

                    st.session_state.user_email = email.strip().lower()
                    st.session_state.session = result.session
                    st.rerun()

                except Exception as e:
                    st.error(f"Login failed: {e}")

conn = duckdb.connect(DB_PATH, read_only=True)

page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Markets", "Platforms", "Movers", "Opportunities", "Health", "Market Detail"],
)

platforms_df = conn.execute("""
    SELECT DISTINCT platform
    FROM market_snapshots
    WHERE platform IS NOT NULL
    ORDER BY platform
""").df()

platforms = ["All"] + platforms_df["platform"].tolist()

selected_platform = st.sidebar.selectbox("Platform", platforms)
search = st.sidebar.text_input("Search Markets", key="sidebar_market_search")

filters = []

if selected_platform != "All":
    filters.append(f"platform = '{selected_platform}'")

if search:
    safe_search = search.lower().replace("'", "''")
    filters.append(f"LOWER(title) LIKE '%{safe_search}%'")

where_clause = ""
if filters:
    where_clause = "WHERE " + " AND ".join(filters)


def latest_extra_filter():
    if filters:
        return "AND " + " AND ".join(filters)
    return ""


def sql_df(query):
    return conn.execute(query).df()


def show_df(df):
    st.dataframe(df, width="stretch", hide_index=True)


if page == "Dashboard":
    total_rows = conn.execute(f"""
        SELECT COUNT(*)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    unique_markets = conn.execute(f"""
        SELECT COUNT(DISTINCT market_id)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    snapshots = conn.execute(f"""
        SELECT COUNT(DISTINCT snapshot_time)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    latest_snapshot = conn.execute(f"""
        SELECT MAX(snapshot_time)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Rows", f"{total_rows:,}")
    c2.metric("Unique Markets", f"{unique_markets:,}")
    c3.metric("Snapshots", f"{snapshots:,}")
    c4.metric("Latest Snapshot", str(latest_snapshot)[:22])

    st.divider()

    st.subheader("Platform Coverage")

    platform_stats = sql_df(f"""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity
        FROM market_snapshots
        {where_clause}
        GROUP BY platform
        ORDER BY rows DESC
    """)

    show_df(platform_stats)

    growth = sql_df(f"""
        SELECT
            snapshot_time,
            COUNT(*) AS rows
        FROM market_snapshots
        {where_clause}
        GROUP BY snapshot_time
        ORDER BY snapshot_time
    """)

    if not growth.empty:
        growth["snapshot_time"] = pd.to_datetime(growth["snapshot_time"])
        fig = px.line(
            growth,
            x="snapshot_time",
            y="rows",
            markers=True,
            title="Rows Collected Over Time",
        )
        st.plotly_chart(fig, width="stretch")

    if not platform_stats.empty:
        fig_rows = px.bar(
            platform_stats,
            x="platform",
            y="rows",
            title="Rows By Platform",
        )
        st.plotly_chart(fig_rows, width="stretch")

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets By Platform",
        )
        st.plotly_chart(fig_unique, width="stretch")

    st.subheader("Snapshot Growth")


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
        {latest_extra_filter()}
        ORDER BY volume DESC NULLS LAST
        LIMIT 100
    """)

    show_df(top_volume)

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
        {latest_extra_filter()}
        ORDER BY platform, volume DESC NULLS LAST
        LIMIT 300
    """)

    show_df(latest_snapshot)


elif page == "Platforms":
    st.subheader("Platform Comparison")

    platform_stats = sql_df(f"""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        {where_clause}
        GROUP BY platform
        ORDER BY rows DESC
    """)

    show_df(platform_stats)

    if not platform_stats.empty:
        fig_rows = px.bar(
            platform_stats,
            x="platform",
            y="rows",
            title="Rows By Platform",
        )
        st.plotly_chart(fig_rows, width="stretch")

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets By Platform",
        )
        st.plotly_chart(fig_unique, width="stretch")


elif page == "Opportunities":

    if not st.session_state.get("user_email"):
        st.warning("Please log in to view the Opportunity Scanner.")
        st.stop()

    user_email = st.session_state.user_email.strip().lower()

    sub_result = supabase.table("user_subscriptions").select("status").eq(
        "email", user_email
    ).execute()

    is_pro = (
        len(sub_result.data) > 0
        and sub_result.data[0].get("status") == "active"
    )

    if not is_pro:
        st.warning("Upgrade to Pro to view the Opportunity Scanner.")

        if st.button("Upgrade to Pro"):
            checkout_session = stripe.checkout.Session.create(
                mode="subscription",
                payment_method_types=["card"],
                line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
                customer_email=user_email,
                success_url=f"http://localhost:8501/?payment=success&email={user_email}",
                cancel_url="http://localhost:8501",
            )
            st.link_button("Open Stripe Checkout", checkout_session.url)

        st.stop()

    st.subheader("Opportunity Scanner")

    def clean_text(value):
        return str(value or "").strip()

    def detect_topic(title):
        t = clean_text(title).lower()

        if "world cup" in t or "fifa" in t:
            return "world_cup"
        if "nomination" in t or "nominee" in t:
            return "nomination"
        if "election" in t or "president" in t:
            return "election"
        if "bitcoin" in t or "btc" in t:
            return "bitcoin"
        if "ethereum" in t or "eth" in t:
            return "ethereum"
        if "fed" in t or "interest rate" in t or "rate cut" in t:
            return "fed_rates"

        return "other"

    def extract_entities(title):
        text = clean_text(title)

        ignore_words = {
            "will", "yes", "no", "who", "what", "when", "where", "which",
            "the", "a", "an", "in", "on", "by", "to", "of", "for", "and",
            "or", "vs", "fifa", "world", "cup", "election", "presidential",
            "president", "nomination", "nominee", "market", "markets"
        }

        entities = set()

        matches = re.findall(
            r"\b(?:[A-Z][a-z]+|[A-Z]{2,}|\d+[A-Za-z]+)(?:\s+(?:[A-Z][a-z]+|[A-Z]{2,}|\d+[A-Za-z]+))*\b",
            text,
        )

        for match in matches:
            cleaned = match.strip().lower()
            parts = [
                p for p in cleaned.split()
                if p not in ignore_words and len(p) > 1
            ]
            if parts:
                entities.add(" ".join(parts))

        aliases = {
            "cr7", "messi", "btc", "bitcoin", "eth", "ethereum",
            "trump", "biden", "kamala", "harris", "newsom",
            "desantis", "vance", "aoc"
        }

        lower_text = text.lower()
        for alias in aliases:
            if alias in lower_text:
                entities.add(alias)

        priority_names = [
            "kamala harris",
            "gavin newsom",
            "gretchen whitmer",
            "josh shapiro",
            "jon ossoff",
            "mark kelly",
            "rahm emanuel",
            "jb pritzker",
            "wes moore",
            "andy beshear",
            "ro khanna",
        ]

        for name in priority_names:
            if name in entities:
                return {name}

        return entities

    latest = sql_df("""
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
            raw_url
        FROM latest
        WHERE yes_price IS NOT NULL
          AND title IS NOT NULL
    """)

    if latest.empty:
        st.info("No opportunity data found yet. Let the scheduler collect more snapshots.")
        st.stop()

    st.sidebar.subheader("Opportunity Filters")

    min_match_score = st.sidebar.slider(
        "Minimum Match Score",
        0.70,
        1.00,
        0.92,
        0.01,
    )

    min_spread = st.sidebar.slider(
        "Minimum Spread",
        0.00,
        0.20,
        0.01,
        0.005,
    )

    min_liquidity = st.sidebar.number_input(
        "Minimum Liquidity",
        value=0.0,
    )

    keyword_filter = st.sidebar.text_input("Opportunity Keyword")

    platform_pair = st.sidebar.selectbox(
        "Platform Pair",
        [
            "All",
            "polymarket ↔ predictit",
            "polymarket ↔ manifold",
            "polymarket ↔ kalshi",
            "predictit ↔ manifold",
            "predictit ↔ kalshi",
            "kalshi ↔ manifold",
        ],
    )

    platform_counts = latest.groupby("platform").agg(
        rows=("market_id", "count"),
        markets=("market_id", "nunique"),
        with_price=("yes_price", "count"),
    ).reset_index()

    st.subheader("Latest Snapshot Platform Counts")
    st.dataframe(platform_counts, width="stretch")

    rows = latest.to_dict("records")
    matches = []
    seen_pairs = set()

    for i, a in enumerate(rows):
        for b in rows[i + 1:]:

            platform_a = clean_text(a.get("platform")).lower()
            platform_b = clean_text(b.get("platform")).lower()

            if platform_a == platform_b:
                continue

            title_a = clean_text(a.get("title"))
            title_b = clean_text(b.get("title"))

            if not title_a or not title_b:
                continue

            topic_a = detect_topic(title_a)
            topic_b = detect_topic(title_b)

            if topic_a != "other" and topic_b != "other" and topic_a != topic_b:
                continue

            office_a = extract_office(title_a)
            office_b = extract_office(title_b)

            if office_a and office_b and office_a != office_b:
                continue

            key_a = canonical_key(title_a)
            key_b = canonical_key(title_b)

            if key_a["year"] and key_b["year"] and key_a["year"] != key_b["year"]:
                continue

            if key_a["party"] and key_b["party"] and key_a["party"] != key_b["party"]:
                continue

            if key_a["event_type"] and key_b["event_type"] and key_a["event_type"] != key_b["event_type"]:
                continue

            if key_a["candidate"] and key_b["candidate"] and key_a["candidate"] != key_b["candidate"]:
                continue

            entities_a = extract_entities(title_a)
            entities_b = extract_entities(title_b)

            if entities_a and entities_b and entities_a.isdisjoint(entities_b):
                continue

            norm_a = normalize_title(title_a)
            norm_b = normalize_title(title_b)

            if not norm_a or not norm_b:
                continue

            score = similarity(norm_a, norm_b)

            if score < min_match_score:
                continue

            price_a = a.get("yes_price")
            price_b = b.get("yes_price")

            if price_a is None or price_b is None:
                continue

            spread = abs(float(price_a) - float(price_b))

            if spread < min_spread:
                continue

            liquidity_a = a.get("liquidity") or 0
            liquidity_b = b.get("liquidity") or 0

            if float(liquidity_a) + float(liquidity_b) < min_liquidity:
                continue

            pair_key = tuple(sorted([
                f"{platform_a}:{a.get('market_id')}",
                f"{platform_b}:{b.get('market_id')}",
            ]))

            if pair_key in seen_pairs:
                continue

            seen_pairs.add(pair_key)

            matches.append({
                "title_a": title_a,
                "title_b": title_b,
                "platform_a": platform_a,
                "platform_b": platform_b,
                "market_id_a": a.get("market_id"),
                "market_id_b": b.get("market_id"),
                "price_a": round(float(price_a), 4),
                "price_b": round(float(price_b), 4),
                "spread": round(float(spread), 4),
                "match_score": round(float(score), 3),
                "topic": topic_a if topic_a != "other" else topic_b,
                "entities_a": ", ".join(sorted(entities_a)),
                "entities_b": ", ".join(sorted(entities_b)),
                "volume_a": a.get("volume"),
                "volume_b": b.get("volume"),
                "liquidity_a": liquidity_a,
                "liquidity_b": liquidity_b,
                "url_a": a.get("raw_url"),
                "url_b": b.get("raw_url"),
            })

    opportunities = pd.DataFrame(matches)

    def pretty_name(x):
        if not x:
            return ""

        names = [n.strip().title() for n in x.split(",") if n.strip()]
        return " / ".join(names)

    if not opportunities.empty:
        opportunities["opportunity"] = opportunities["entities_a"].apply(pretty_name)
        opportunities.loc[opportunities["opportunity"] == "", "opportunity"] = opportunities["title_a"]

    if keyword_filter and not opportunities.empty:
        keyword = keyword_filter.lower()
        opportunities = opportunities[
            opportunities["title_a"].str.lower().str.contains(keyword, na=False)
            | opportunities["title_b"].str.lower().str.contains(keyword, na=False)
            | opportunities["opportunity"].str.lower().str.contains(keyword, na=False)
        ]

    if platform_pair != "All" and not opportunities.empty:
        left, right = [x.strip() for x in platform_pair.split("↔")]
        opportunities = opportunities[
            ((opportunities["platform_a"] == left) & (opportunities["platform_b"] == right))
            | ((opportunities["platform_a"] == right) & (opportunities["platform_b"] == left))
        ]

    if opportunities.empty:
        st.info("No cross-platform opportunities found with the current filters.")
    else:
        opportunities["edge_pct"] = opportunities["spread"] * 100
        opportunities["opportunity_score"] = (
            opportunities["spread"] * 100 + opportunities["match_score"] * 10
        ).round(2)

        sort_method = st.selectbox(
            "Rank Opportunities By",
            ["Opportunity Score", "Spread", "Liquidity", "Match Score"],
        )

        if sort_method == "Opportunity Score":
            opportunities = opportunities.sort_values("opportunity_score", ascending=False)
        elif sort_method == "Spread":
            opportunities = opportunities.sort_values("spread", ascending=False)
        elif sort_method == "Liquidity":
            opportunities["total_liquidity"] = (
                opportunities["liquidity_a"].fillna(0)
                + opportunities["liquidity_b"].fillna(0)
            )
            opportunities = opportunities.sort_values("total_liquidity", ascending=False)
        else:
            opportunities = opportunities.sort_values("match_score", ascending=False)

        opportunities["buy_platform"] = opportunities.apply(
            lambda r: r["platform_a"] if r["price_a"] < r["price_b"] else r["platform_b"],
            axis=1,
        )
        opportunities["buy_price"] = opportunities.apply(
            lambda r: min(r["price_a"], r["price_b"]),
            axis=1,
        )
        opportunities["sell_platform"] = opportunities.apply(
            lambda r: r["platform_b"] if r["price_a"] < r["price_b"] else r["platform_a"],
            axis=1,
        )
        opportunities["sell_price"] = opportunities.apply(
            lambda r: max(r["price_a"], r["price_b"]),
            axis=1,
        )
        opportunities["profit_per_share"] = (
            opportunities["sell_price"] - opportunities["buy_price"]
        ).round(4)

        opportunities = opportunities.head(100)

        c1, c2, c3 = st.columns(3)
        c1.metric("Candidates", len(opportunities))
        c2.metric("Largest Spread", round(opportunities["spread"].max(), 4))
        c3.metric("Average Spread", round(opportunities["spread"].mean(), 4))

        opportunities["market_a_link"] = opportunities["url_a"].apply(
            lambda x: x if str(x).startswith("http") else f"https://polymarket.com/market/{x}"
        )
        opportunities["market_b_link"] = opportunities["url_b"].apply(
            lambda x: x if str(x).startswith("http") else f"https://www.predictit.org/markets/detail/{x}"
        )

        display_df = opportunities[
            [
                "opportunity",
                "buy_platform",
                "buy_price",
                "sell_platform",
                "sell_price",
                "spread",
                "edge_pct",
                "profit_per_share",
                "match_score",
                "opportunity_score",
                "market_a_link",
                "market_b_link",
            ]
        ]
        display_df_numeric = display_df.copy()

        platform_labels = {
            "polymarket": "🟣 Polymarket",
            "predictit": "🔵 PredictIt",
            "kalshi": "🟠 Kalshi",
            "manifold": "🟢 Manifold",
        }
        display_df["buy_platform"] = display_df["buy_platform"].replace(platform_labels)
        display_df["sell_platform"] = display_df["sell_platform"].replace(platform_labels)

        display_df = display_df.rename(
            columns={
                "opportunity": "Opportunity",
                "buy_platform": "Buy On",
                "buy_price": "Buy Price",
                "sell_platform": "Sell On",
                "sell_price": "Sell Price",
                "spread": "Spread",
                "edge_pct": "Edge %",
                "profit_per_share": "Profit/Share",
                "match_score": "Match",
                "opportunity_score": "Score",
                "market_a_link": "Market A",
                "market_b_link": "Market B",
            }
        )

        for col in ["Buy Price", "Sell Price", "Spread", "Profit/Share", "Match"]:
            display_df[col] = display_df[col].apply(lambda x: f"{x:.2%}")

        display_df["Edge %"] = display_df["Edge %"].apply(lambda x: f"{x:.2f}%")
        display_df["Score"] = display_df["Score"].apply(lambda x: f"{x:.2f}")

        styled = (
            display_df.style
            .background_gradient(
                subset=["Edge %"],
                cmap="Greens",
                gmap=display_df_numeric["edge_pct"],
            )
            .background_gradient(
                subset=["Profit/Share"],
                cmap="Greens",
                gmap=display_df_numeric["profit_per_share"],
            )
            .background_gradient(
                subset=["Score"],
                cmap="Greens",
                gmap=display_df_numeric["opportunity_score"],
            )
        )

        st.dataframe(styled, use_container_width=True)

        st.subheader("Top Opportunity Candidates")
        chart_df = opportunities.head(25).copy()
        fig = px.bar(
            chart_df,
            x="spread",
            y="opportunity",
            orientation="h",
            title="Top Cross-Platform Spreads",
        )
        st.plotly_chart(fig, width="stretch")

elif page == "Movers":
    st.subheader("Market Movers")

    movers = sql_df(f"""
        WITH market_changes AS (
            SELECT
                platform,
                market_id,
                title,
                COUNT(*) AS snapshots,
                MIN(yes_price) AS low_price,
                MAX(yes_price) AS high_price,
                FIRST(yes_price ORDER BY snapshot_time) AS first_price,
                LAST(yes_price ORDER BY snapshot_time) AS last_price,
                LAST(yes_price ORDER BY snapshot_time)
                    - FIRST(yes_price ORDER BY snapshot_time) AS price_change,
                LAST(volume ORDER BY snapshot_time)
                    - FIRST(volume ORDER BY snapshot_time) AS volume_change,
                LAST(liquidity ORDER BY snapshot_time)
                    - FIRST(liquidity ORDER BY snapshot_time) AS liquidity_change
            FROM market_snapshots
            WHERE yes_price IS NOT NULL
            {latest_extra_filter()}
            GROUP BY platform, market_id, title
            HAVING COUNT(*) >= 3
        )
        SELECT *
        FROM market_changes
        ORDER BY ABS(price_change) DESC NULLS LAST
        LIMIT 100
    """)

    display_cols = [
        "platform",
        "market_id",
        "title",
        "snapshots",
        "low_price",
        "high_price",
        "first_price",
        "last_price",
        "price_change",
        "volume_change",
        "liquidity_change",
    ]

    if movers.empty:
        st.info("No movers found yet. Let the scheduler collect more snapshots.")
    else:
        show_df(movers[display_cols])

        st.subheader("Top Gainers")
        gainers = movers.sort_values("price_change", ascending=False).head(25)
        show_df(gainers[display_cols])

        st.subheader("Top Losers")
        losers = movers.sort_values("price_change", ascending=True).head(25)
        show_df(losers[display_cols])

        st.subheader("Volume Movers")
        volume_movers = movers.sort_values("volume_change", ascending=False).head(25)
        show_df(volume_movers[display_cols])

        st.subheader("Liquidity Movers")
        liquidity_movers = movers.sort_values("liquidity_change", ascending=False).head(25)
        show_df(liquidity_movers[display_cols])


elif page == "Health":
    st.subheader("Market Health")

    health = sql_df(f"""
        SELECT
            platform,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS total_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        {where_clause}
        GROUP BY platform
        ORDER BY total_rows DESC
    """)

    if health.empty:
        st.info("No market health data found.")
    else:
        display_health = health.rename(
            columns={
                "platform": "Platform",
                "total_rows": "Snapshots",
                "total_markets": "Markets",
                "avg_volume": "Avg Volume",
                "avg_liquidity": "Avg Liquidity",
                "first_snapshot": "First Snapshot",
                "latest_snapshot": "Latest Snapshot",
            }
        )
        st.dataframe(display_health, use_container_width=True)

        col1, col2 = st.columns(2)

        with col1:
            fig_rows = px.bar(
                health,
                x="platform",
                y="total_rows",
                title="Rows By Platform",
            )
            st.plotly_chart(fig_rows, width="stretch")

        with col2:
            fig_markets = px.bar(
                health,
                x="platform",
                y="total_markets",
                title="Markets By Platform",
            )
            st.plotly_chart(fig_markets, width="stretch")


elif page == "Market Detail":
    st.subheader("Market Detail")

    market_where = where_clause if where_clause else "WHERE market_id IS NOT NULL"

    if where_clause:
        market_where += " AND market_id IS NOT NULL"

    markets = sql_df(f"""
        SELECT DISTINCT
            market_id,
            title,
            platform
        FROM market_snapshots
        {market_where}
        ORDER BY title
        LIMIT 3000
    """)

    if markets.empty:
        st.info("No markets found.")
    else:
        markets["label"] = (
            markets["title"].fillna("").str[:120]
            + " | "
            + markets["market_id"].fillna("")
        )

        selected_label = st.selectbox(
            "Select a market",
            markets["label"].tolist(),
            key="market_detail_selected_market",
        )

        selected_market_id = markets.loc[
            markets["label"] == selected_label,
            "market_id",
        ].iloc[0]

        history = conn.execute("""
            SELECT
                snapshot_time,
                platform,
                market_id,
                title,
                yes_price,
                no_price,
                volume,
                liquidity,
                status,
                raw_url
            FROM market_snapshots
            WHERE market_id = ?
            ORDER BY snapshot_time
        """, [selected_market_id]).df()

        show_df(history.tail(25))

        if not history.empty:
            history["snapshot_time"] = pd.to_datetime(history["snapshot_time"])
            latest = history.sort_values("snapshot_time").iloc[-1]

            c1, c2, c3 = st.columns(3)
            c1.metric("Latest YES", latest.get("yes_price"))
            c2.metric("Latest Volume", latest.get("volume"))
            c3.metric("Observations", len(history))

            st.subheader("YES Price History")
            price_df = history.dropna(subset=["yes_price"])

            if not price_df.empty:
                fig_price = px.line(
                    price_df,
                    x="snapshot_time",
                    y="yes_price",
                    markers=True,
                    title="YES Price History",
                )
                st.plotly_chart(fig_price, width="stretch")
            else:
                st.info("No YES price history available.")

            st.subheader("Volume History")
            volume_df = history.dropna(subset=["volume"])

            if not volume_df.empty:
                fig_volume = px.bar(
                    volume_df,
                    x="snapshot_time",
                    y="volume",
                    title="Volume History",
                )
                st.plotly_chart(fig_volume, width="stretch")
            else:
                st.info("No volume history available.")

            st.subheader("Liquidity History")
            liquidity_df = history.dropna(subset=["liquidity"])

            if not liquidity_df.empty:
                fig_liquidity = px.area(
                    liquidity_df,
                    x="snapshot_time",
                    y="liquidity",
                    title="Liquidity History",
                )
                st.plotly_chart(fig_liquidity, width="stretch")
            else:
                st.info("No liquidity history available.")

            csv = history.to_csv(index=False)

            st.download_button(
                "Download Market CSV",
                csv,
                file_name=f"{selected_market_id}.csv",
                mime="text/csv",
            )

conn.close()