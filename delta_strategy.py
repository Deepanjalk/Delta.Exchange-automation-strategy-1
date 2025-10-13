import ccxt
import os
import pandas as pd
import pandas_ta as ta
import time

# --- State Management ---
trade_count = 0
atm_strike_price = None
straddle_positions = {'call': None, 'put': None}

def get_atm_strike_price(exchange, symbol):
    """
    Identifies the at-the-money (ATM) strike price for a given symbol.
    """
    try:
        ticker = exchange.fetch_ticker(symbol)
        underlying_price = ticker['last']
        print(f"Underlying price for {symbol}: {underlying_price}")
        exchange.load_markets(True)
        markets = exchange.markets
        options = {
            s: m for s, m in markets.items()
            if m.get('option') and m.get('base') == symbol.split('/')[0]
        }
        if not options:
            print(f"No options found for {symbol.split('/')[0]}")
            return None
        closest_strike = None
        min_diff = float('inf')
        for s, m in options.items():
            strike = m.get('strike')
            if strike:
                diff = abs(strike - underlying_price)
                if diff < min_diff:
                    min_diff = diff
                    closest_strike = strike
        print(f"ATM Strike Price: {closest_strike}")
        return closest_strike
    except ccxt.errors.ExchangeError as e:
        print(f"An error occurred: {e}")
        return None

def get_historical_data(exchange, symbol, timeframe='15m', limit=200):
    """
    Fetches historical OHLCV data.
    """
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except ccxt.errors.ExchangeError as e:
        print(f"An error occurred while fetching historical data for {symbol}: {e}")
        return None

def get_straddle_graph(exchange, call_symbol, put_symbol, timeframe='15m', limit=200):
    """
    Creates a straddle graph by combining the close prices of a call and put option.
    """
    call_df = get_historical_data(exchange, call_symbol, timeframe, limit)
    put_df = get_historical_data(exchange, put_symbol, timeframe, limit)

    if call_df is None or put_df is None:
        return None

    straddle_df = pd.DataFrame(index=call_df.index)
    straddle_df['close'] = call_df['close'] + put_df['close']
    # pandas-ta requires high, low, close
    straddle_df['high'] = straddle_df['close']
    straddle_df['low'] = straddle_df['close']

    return straddle_df

def get_supertrend(df, length=10, multiplier=3):
    """
    Calculates the Supertrend indicator.
    """
    if df is None or df.empty:
        return None
    df.ta.supertrend(length=length, multiplier=multiplier, append=True)
    return df

def place_order(exchange, symbol, order_type, side, amount, price=None):
    """
    Places an order.
    """
    try:
        print(f"Placing {side} {order_type} order for {amount} contracts of {symbol}...")
        if order_type == 'limit':
            return exchange.create_order(symbol, order_type, side, amount, price)
        else:
            return exchange.create_order(symbol, order_type, side, amount)
    except ccxt.errors.ExchangeError as e:
        print(f"An error occurred while placing an order: {e}")
        return None

def find_option_symbols(exchange, underlying_symbol, strike_price):
    """
    Finds the call and put option symbols for a given strike price.
    """
    exchange.load_markets(True)
    markets = exchange.markets
    call_symbol, put_symbol = None, None
    for symbol, market in markets.items():
        if market.get('strike') == strike_price and market.get('base') == underlying_symbol.split('/')[0]:
            if market.get('optionType') == 'call':
                call_symbol = symbol
            elif market.get('optionType') == 'put':
                put_symbol = symbol
    return call_symbol, put_symbol


def run_strategy(exchange, symbol):
    """
    Runs the main trading strategy.
    """
    global trade_count, atm_strike_price, straddle_positions
    print("\n" + "="*50)
    print(f"Executing strategy run. Trade count: {trade_count}")

    if trade_count >= 3:
        print("Maximum trade count reached for the day. Stopping.")
        return

    # 1. Daily Setup: Identify ATM strike if not already set
    if atm_strike_price is None:
        print("Identifying ATM strike price for the day...")
        atm_strike_price = get_atm_strike_price(exchange, symbol)
        if atm_strike_price is None:
            print("Could not determine ATM strike price. Exiting.")
            return

    # Find the symbols for the ATM call and put options
    call_symbol, put_symbol = find_option_symbols(exchange, symbol, atm_strike_price)
    if not all([call_symbol, put_symbol]):
        print(f"Could not find option symbols for strike {atm_strike_price}. Exiting.")
        return

    print(f"Using ATM Call: {call_symbol}, ATM Put: {put_symbol}")

    # 2. Create Straddle Graph and get Supertrend
    straddle_df = get_straddle_graph(exchange, call_symbol, put_symbol)
    if straddle_df is None:
        print("Could not create straddle graph. Exiting.")
        return
    straddle_df = get_supertrend(straddle_df)

    if straddle_df is None:
        print("Could not calculate Supertrend. Exiting.")
        return

    latest_signal = straddle_df.iloc[-1]
    supertrend_direction = latest_signal.get('SUPERTd_10_3')

    # 3. Trade Rules
    if supertrend_direction == -1: # SELL Signal
        print("Supertrend is in SELL mode.")
        if straddle_positions['call'] is None and straddle_positions['put'] is None:
            print("Case A: No open positions. Creating straddle.")
            # Sell ATM Call + ATM Put
            call_order = place_order(exchange, call_symbol, 'market', 'sell', 1)
            put_order = place_order(exchange, put_symbol, 'market', 'sell', 1)
            if call_order and put_order:
                straddle_positions['call'] = call_order
                straddle_positions['put'] = put_order
                trade_count += 1
                print("Straddle created successfully.")
        elif straddle_positions['call'] is None or straddle_positions['put'] is None:
             print("Case C: One leg open. Reconstructing straddle.")
             if straddle_positions['call'] is None:
                call_order = place_order(exchange, call_symbol, 'market', 'sell', 1)
                if call_order:
                    straddle_positions['call'] = call_order
                    print("Reconstructed straddle by selling call.")
             if straddle_positions['put'] is None:
                put_order = place_order(exchange, put_symbol, 'market', 'sell', 1)
                if put_order:
                    straddle_positions['put'] = put_order
                    print("Reconstructed straddle by selling put.")


    elif supertrend_direction == 1: # BUY Signal
        print("Supertrend is in BUY mode.")
        if straddle_positions['call'] and straddle_positions['put']:
            print("Case B: Straddle open. Closing the gaining leg.")
            # Determine which leg to close
            call_ticker = exchange.fetch_ticker(call_symbol)
            put_ticker = exchange.fetch_ticker(put_symbol)

            # Simplified logic: assume underlying move determines gainer
            underlying_ticker = exchange.fetch_ticker(symbol)
            if underlying_ticker['last'] > atm_strike_price: # Underlying went up
                print("Underlying is up. Closing call position.")
                close_order = place_order(exchange, call_symbol, 'market', 'buy', 1)
                if close_order:
                    straddle_positions['call'] = None
            else: # Underlying went down
                print("Underlying is down. Closing put position.")
                close_order = place_order(exchange, put_symbol, 'market', 'buy', 1)
                if close_order:
                    straddle_positions['put'] = None
    else:
        print("No valid Supertrend signal found.")

    print(f"Current positions: {straddle_positions}")
    print("="*50 + "\n")

# Reset state for the next day (to be called by the scheduler)
def reset_daily_state():
    global trade_count, atm_strike_price, straddle_positions
    print("Resetting daily state for new trading session.")
    trade_count = 0
    atm_strike_price = None
    straddle_positions = {'call': None, 'put': None}
