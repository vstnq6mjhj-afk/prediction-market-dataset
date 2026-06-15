from pathlib import Path
import pandas as pd


def ensure_directory(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def list_csv_files(directory):
    return sorted(Path(directory).glob("*.csv"))


def save_csv(df, output_path):
    ensure_directory(Path(output_path).parent)
    df.to_csv(output_path, index=False)


def load_csv(path):
    return pd.read_csv(path)


def file_exists(path):
    return Path(path).exists()


def delete_file(path):
    p = Path(path)
    if p.exists():
        p.unlink()


if __name__ == "__main__":
    print("File utils initialized.")