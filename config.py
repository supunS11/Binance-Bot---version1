import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

SYMBOLS = os.getenv("SYMBOLS").split(",")
#TIMEFRAME = os.getenv("TIMEFRAME")

TREND_TIMEFRAME = os.getenv("TREND_TIMEFRAME")

CONFIRMATION_TIMEFRAME = os.getenv("CONFIRMATION_TIMEFRAME")

ENTRY_TIMEFRAME = os.getenv("ENTRY_TIMEFRAME")

LEVERAGE = int(os.getenv("LEVERAGE"))

RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE"))

ROI_PERCENT_TP = float(os.getenv("ROI_PERCENT_TP"))

ROI_PERCENT_SL = float(os.getenv("ROI_PERCENT_SL"))

MARGIN_TYPE = os.getenv("MARGIN_TYPE","ISOLATED").upper()

MODE = os.getenv("MODE")

MAX_TOTAL_POSITIONS = (
    int(os.getenv("MAX_TOTAL_POSITIONS"))
    if os.getenv("MAX_TOTAL_POSITIONS")
    else None
)

MAX_BUY_POSITIONS = (
    int(os.getenv("MAX_BUY_POSITIONS"))
    if os.getenv("MAX_BUY_POSITIONS")
    else None
)

MAX_SELL_POSITIONS = (
    int(os.getenv("MAX_SELL_POSITIONS"))
    if os.getenv("MAX_SELL_POSITIONS")
    else None
)

TESTNET = os.getenv("TESTNET") == "False"