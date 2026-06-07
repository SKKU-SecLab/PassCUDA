import pandas as pd

def CAPTCHA_solver():
    input("CAPTCHA done?: ")

def get_email(domain: str, csv_path: str = "credentials.csv") -> list:
    df = pd.read_csv(csv_path)
    return df.loc[df["domain"] == domain, "ID"].tolist()

def get_password(domain: str, csv_path: str = "credentials.csv") -> list:
    df = pd.read_csv(csv_path)
    return df.loc[df["domain"] == domain, "Password"].tolist()