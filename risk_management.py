import config

from exchange import get_symbol_precision

def calculate_position_size(balance, price, symbol):

    risk_amount = (
        balance * config.RISK_PER_TRADE
    ) / 100

    position_size = (
        risk_amount * config.LEVERAGE
    ) / price

    precision = get_symbol_precision(symbol)

    return round(position_size, precision)