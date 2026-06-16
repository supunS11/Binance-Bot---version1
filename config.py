import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

SYMBOLS = os.getenv("SYMBOLS").split(",")

TREND_TIMEFRAME = os.getenv("TREND_TIMEFRAME")
CONFIRMATION_TIMEFRAME = os.getenv("CONFIRMATION_TIMEFRAME")
ENTRY_TIMEFRAME = os.getenv("ENTRY_TIMEFRAME")
SL_TIMEFRAME = os.getenv("SL_TIMEFRAME")

LEVERAGE = int(os.getenv("LEVERAGE"))

MAX_SL_ROI = float(os.getenv("MAX_SL_ROI"))

RR_TAKE_PROFIT = float(os.getenv("RR_TAKE_PROFIT", 1.2))

# =========================
# TAKE PROFIT MODE
# =========================
STATIC_TP_ENABLED = os.getenv("STATIC_TP_ENABLED", "False") == "True"
STATIC_TP_ROI = float(os.getenv("STATIC_TP_ROI", os.getenv("ROI_PERCENT_TP", 10)))

# =========================
# HIGH CONFIDENCE LEVERAGE
# =========================
HIGH_CONFIDENCE_LEVERAGE_ENABLED = os.getenv("HIGH_CONFIDENCE_LEVERAGE_ENABLED", "False") == "True"
HIGH_CONFIDENCE_THRESHOLD = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", 100))
HIGH_CONFIDENCE_LEVERAGE = int(os.getenv("HIGH_CONFIDENCE_LEVERAGE", LEVERAGE))

TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "False") == "True"
TRAILING_TP_PERCENT = float(os.getenv("TRAILING_TP_PERCENT", 50))
TRAILING_CALLBACK_RATE = float(os.getenv("TRAILING_CALLBACK_RATE", 0.7))

MARGIN_TYPE = os.getenv("MARGIN_TYPE", "ISOLATED").upper()
MODE = os.getenv("MODE")

MARGIN_PER_TRADE = float(os.getenv("MARGIN_PER_TRADE", 6))

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