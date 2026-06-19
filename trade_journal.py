import csv
import os

import config
from logger import log_error, log_warning


FIELDS = [
    "event",
    "trade_id",
    "symbol",
    "side",
    "time",
    "entry_time",
    "exit_time",
    "duration_seconds",
    "entry_price",
    "exit_price",
    "quantity",
    "tp_price",
    "sl_price",
    "rr",
    "target_roi",
    "profit_protection_trigger_roi",
    "realized_pnl",
    "outcome",
    "confidence",
    "leverage",
    "ema20_distance_pct",
    "atr_pct",
    "adx",
    "rsi",
    "macd",
    "macd_signal",
    "vwap_side",
    "btc_trend",
    "btc_corr",
    "relative_strength",
    "bullish_structure",
    "bearish_structure",
    "bullish_bos",
    "bearish_bos",
    "bullish_choch",
    "bearish_choch",
    "entry_quality",
    "peak_roi",
    "last_roi",
    "last_unrealized_pnl",
    "sl_management_stage",
    "sl_move_count",
    "early_exit_reason",
]


def _journal_path():
    return getattr(config, "TRADE_JOURNAL_PATH", "logs/trade_journal.csv")


def _has_current_header(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return False

        with open(path, "r", newline="", encoding="utf-8") as journal:
            reader = csv.reader(journal)
            header = next(reader, [])

        return header == FIELDS

    except Exception as e:
        log_error(f"TRADE JOURNAL HEADER CHECK ERROR: {e}")
        return True


def _archive_mismatched_journal(path):
    if _has_current_header(path):
        return True

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False

    base, ext = os.path.splitext(path)
    archive_path = f"{base}.legacy{ext}"
    counter = 1

    while os.path.exists(archive_path):
        archive_path = f"{base}.legacy-{counter}{ext}"
        counter += 1

    os.replace(path, archive_path)
    log_warning(
        f"TRADE JOURNAL HEADER MISMATCH | ARCHIVED OLD FILE: {archive_path}"
    )
    return False


def append_trade_event(data):

    try:
        path = _journal_path()
        folder = os.path.dirname(path)

        if folder:
            os.makedirs(folder, exist_ok=True)

        file_exists = _archive_mismatched_journal(path)

        with open(path, "a", newline="", encoding="utf-8") as journal:
            writer = csv.DictWriter(journal, fieldnames=FIELDS)

            if not file_exists:
                writer.writeheader()

            row = {field: data.get(field, "") for field in FIELDS}
            writer.writerow(row)

    except Exception as e:
        log_error(f"TRADE JOURNAL ERROR: {e}")
