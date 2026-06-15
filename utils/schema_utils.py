import pandas as pd
def normalize_market_schema(df):
    required_columns = [
        "market_id",
        "title",
        "platform",
        "category",
        "yes_price",
        "no_price",
        "volume",
        "liquidity",
    ]

    for col in required_columns:
        if col not in df.columns:
            if col in ["yes_price", "no_price", "volume", "liquidity"]:
                df[col] = 0.0
            else:
                df[col] = "unknown"

    for col in ["yes_price", "no_price", "volume", "liquidity"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return df

def normalize_alpha_schema(df):
    """
    Standardize alpha forecast dataframe columns.
    """

    required_columns = [
        "market_id",
        "title",
        "platform",
        "alpha_score",
        "signal_strength",
    ]

    for col in required_columns:
        if col not in df.columns:
            if col in [
                "alpha_score",
                "signal_strength",
            ]:
                df[col] = 0.0
            else:
                df[col] = "unknown"

    numeric_columns = [
        "alpha_score",
        "signal_strength",
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        ).fillna(0)

    return df


def normalize_signal_schema(df):
    """
    Standardize autonomous signal dataframe columns.
    """

    required_columns = [
        "market_id",
        "title",
        "platform",
        "signal_strength",
    ]

    for col in required_columns:
        if col not in df.columns:
            if col == "signal_strength":
                df[col] = 0.0
            else:
                df[col] = "unknown"

    df["signal_strength"] = pd.to_numeric(
        df["signal_strength"],
        errors="coerce",
    ).fillna(0)

    return df


def normalize_portfolio_schema(df):
    """
    Standardize portfolio dataframe columns.
    """

    required_columns = [
        "market_id",
        "title",
        "platform",
        "alpha_score",
        "signal_strength",
        "position_size",
        "portfolio_action",
    ]

    for col in required_columns:
        if col not in df.columns:
            if col in [
                "alpha_score",
                "signal_strength",
                "position_size",
            ]:
                df[col] = 0.0
            else:
                df[col] = "unknown"

    numeric_columns = [
        "alpha_score",
        "signal_strength",
        "position_size",
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        ).fillna(0)

    return df


if __name__ == "__main__":
    print("Schema utils initialized.")