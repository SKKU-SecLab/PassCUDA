import pandas as pd


def get_email(domain: str, csv_path: str = "credentials.csv") -> list:
    df = pd.read_csv(csv_path)
    return df.loc[df["domain"] == domain, "ID"].tolist()


def get_password(domain: str, csv_path: str = "credentials.csv") -> list:
    df = pd.read_csv(csv_path)
    return df.loc[df["domain"] == domain, "Password"].tolist()


def get_2FA_code() -> str:
    code = input("input code: ")
    return code
