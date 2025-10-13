import ccxt
import os
import pandas as pd
import pandas_ta as ta
import time

# --- State Management ---
trade_count = 0
atm_strike_price = None
straddle_positions = {'call': None, 'put': None}
straddle_data_df = pd.DataFrame() # For collecting daily straddle data

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
    Creates a straddle graph by combining the OHLC prices of a call and put option.
    """
    call_df = get_historical_data(exchange, call_symbol, timeframe, limit)
    put_df = get_historical_data(exchange, put_symbol, timeframe, limit)

    if call_df is None or put_df is None:
        return None

    # Use pd.merge to ensure timestamps are aligned correctly
    combined_df = pd.merge(
        call_df,
        put_df,
        left_index=True,
        right_index=True,
        how='inner',
        suffixes=('_call', '_put')
    )

    # Create the straddle DataFrame
    straddle_df = pd.DataFrame(index=combined_df.index)
    straddle_df['close'] = combined_df['close_call'] + combined_df['close_put']
    straddle_df['high'] = combined_df['high_call'] + combined_df['high_put']
    straddle_df['low'] = combined_df['low_call'] + combined_df['low_put']

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

def determine_trade_action(supertrend_direction, straddle_positions):
    """
    Determines the trade action based on the Supertrend signal and current positions.
    """
    is_call_open = straddle_positions.get('call') is not None
    is_put_open = straddle_positions.get('put') is not None

    # Case A: Supertrend is SELL and no positions are open -> Create a straddle
    if supertrend_direction == -1 and not is_call_open and not is_put_open:
        return 'CREATE_STRADDLE'

    # Case B: Supertrend is BUY and both legs of the straddle are open -> Close the losing leg
    elif supertrend_direction == 1 and is_call_open and is_put_open:
        return 'CLOSE_LOSING_LEG'

    # Case C: Supertrend flips back to SELL and one leg is open -> Reconstruct the straddle
    elif supertrend_direction == -1 and (is_call_open ^ is_put_open):
        return 'RECONSTRUCT_STRADDLE'

    # Hold positions if the signal is unchanged or doesn't warrant action
    else:
        return 'HOLD'

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


def set_daily_atm_strike(exchange, symbol):
    """
    Identifies and sets the ATM strike price for the day.
    """
    global atm_strike_price
    print("Identifying ATM strike price for the day...")
    atm_strike_price = get_atm_strike_price(exchange, symbol)
    if atm_strike_price is None:
        print("Could not determine ATM strike price. Trading will be paused.")

def run_scheduled_strategy(exchange, symbol):
    """
    Runs the main trading strategy logic at scheduled intervals.
    """
    global trade_count, atm_strike_price, straddle_positions

    if atm_strike_price is None:
        print("ATM strike price not set for the day. Cannot run strategy.")
        return

    print("\n" + "="*50)
    print(f"Executing strategy run. Trade count: {trade_count}")

    if trade_count >= 3:
        print("Maximum trade count reached for the day. Stopping.")
        return

    # Find the symbols for the ATM call and put options
    call_symbol, put_symbol = find_option_symbols(exchange, symbol, atm_strike_price)
    if not all([call_symbol, put_symbol]):
        print(f"Could not find option symbols for strike {atm_strike_price}. Exiting.")
        return

    print(f"Using ATM Call: {call_symbol}, ATM Put: {put_symbol}")

    # --- Incremental Data Collection for Supertrend ---
    global straddle_data_df

    # Fetch the latest 2 candles to ensure we get a closed candle
    call_df = get_historical_data(exchange, call_symbol, timeframe='15m', limit=2)
    put_df = get_historical_data(exchange, put_symbol, timeframe='15m', limit=2)

    if call_df is None or put_df is None or call_df.empty or put_df.empty:
        print("Could not fetch latest candle data. Exiting.")
        return

    latest_call_candle = call_df.iloc[-1]
    latest_put_candle = put_df.iloc[-1]

    # Ensure timestamps match
    if latest_call_candle.name != latest_put_candle.name:
        print("Candle timestamps do not match. Exiting.")
        return

    # Construct the straddle candle
    straddle_candle = {
        'timestamp': latest_call_candle.name,
        'open': latest_call_candle['open'] + latest_put_candle['open'],
        'high': latest_call_candle['high'] + latest_put_candle['high'],
        'low': latest_call_candle['low'] + latest_put_candle['low'],
        'close': latest_call_candle['close'] + latest_put_candle['close'],
        'volume': latest_call_candle['volume'] + latest_put_candle['volume']
    }

    # Append to the daily DataFrame, avoiding duplicates
    if straddle_candle['timestamp'] not in straddle_data_df.index:
        straddle_data_df = straddle_data_df.append(pd.Series(straddle_candle, name=straddle_candle['timestamp']))
        straddle_data_df.index.name = 'timestamp'

    print(f"Collected {len(straddle_data_df)} candles for the day.")

    # We need at least 'length' candles to calculate Supertrend
    if len(straddle_data_df) < 10:
        print("Not enough data to calculate Supertrend yet. Waiting for more candles.")
        return

    # Calculate Supertrend on the collected data
    supertrend_df = get_supertrend(straddle_data_df.copy()) # Use a copy to avoid modifying the original df

    if supertrend_df is None or supertrend_df.empty:
        print("Could not calculate Supertrend. Exiting.")
        return

    latest_signal = supertrend_df.iloc[-1]
    supertrend_direction = latest_signal.get('SUPERTd_10_3')

    # Determine and execute trade action
    action = determine_trade_action(supertrend_direction, straddle_positions)
    print(f"Supertrend Signal: {'BUY' if supertrend_direction == 1 else 'SELL' if supertrend_direction == -1 else 'NONE'}")
    print(f"Determined Action: {action}")

    if action == 'CREATE_STRADDLE':
        print("Case A: Creating straddle.")
        call_order = place_order(exchange, call_symbol, 'market', 'sell', 1)
        put_order = place_order(exchange, put_symbol, 'market', 'sell', 1)
        if call_order and put_order:
            call_entry_price = call_order.get('average') or call_order.get('price')
            put_entry_price = put_order.get('average') or put_order.get('price')

            if call_entry_price and put_entry_price:
                straddle_positions['call'] = {'order_id': call_order['id'], 'entry_price': call_entry_price}
                straddle_positions['put'] = {'order_id': put_order['id'], 'entry_price': put_entry_price}
                trade_count += 1
                print(f"Straddle created. Call entry: {call_entry_price}, Put entry: {put_entry_price}")
            else:
                print("Could not determine entry prices for straddle. Orders might not have filled.")

    elif action == 'RECONSTRUCT_STRADDLE':
        print("Case C: Reconstructing straddle.")
        if straddle_positions.get('call') is None:
            call_order = place_order(exchange, call_symbol, 'market', 'sell', 1)
            if call_order:
                call_entry_price = call_order.get('average') or call_order.get('price')
                if call_entry_price:
                    straddle_positions['call'] = {'order_id': call_order['id'], 'entry_price': call_entry_price}
                    print(f"Reconstructed straddle by selling call at {call_entry_price}.")
                else:
                    print("Could not determine entry price for call. Order might not have filled.")

        if straddle_positions.get('put') is None:
            put_order = place_order(exchange, put_symbol, 'market', 'sell', 1)
            if put_order:
                put_entry_price = put_order.get('average') or put_order.get('price')
                if put_entry_price:
                    straddle_positions['put'] = {'order_id': put_order['id'], 'entry_price': put_entry_price}
                    print(f"Reconstructed straddle by selling put at {put_entry_price}.")
                else:
                    print("Could not determine entry price for put. Order might not have filled.")

    elif action == 'CLOSE_LOSING_LEG':
        print("Case B: Closing the losing leg.")
        try:
            call_ticker = exchange.fetch_ticker(call_symbol)
            put_ticker = exchange.fetch_ticker(put_symbol)

            call_current_price = call_ticker['last']
            put_current_price = put_ticker['last']

            call_entry_price = straddle_positions['call']['entry_price']
            put_entry_price = straddle_positions['put']['entry_price']

            # For a sell position, profit = entry_price - current_price
            call_pnl = call_entry_price - call_current_price
            put_pnl = put_entry_price - put_current_price

            print(f"Call PnL: {call_pnl:.4f}, Put PnL: {put_pnl:.4f}")

            # The strategy is to close the LOSING leg (the one with the lower PnL).
            # A rising premium on a short position results in a loss.
            if call_pnl < put_pnl:
                print("Call is the losing leg. Closing call position.")
                close_order = place_order(exchange, call_symbol, 'market', 'buy', 1)
                if close_order:
                    straddle_positions['call'] = None
            else:
                print("Put is the losing leg. Closing put position.")
                close_order = place_order(exchange, put_symbol, 'market', 'buy', 1)
                if close_order:
                    straddle_positions['put'] = None
        except (ccxt.errors.ExchangeError, KeyError) as e:
            print(f"An error occurred while closing losing leg: {e}")

    elif action == 'HOLD':
        print("Holding positions.")

    else:
        print("No trade action taken.")

    print(f"Current positions: {straddle_positions}")
    print("="*50 + "\n")

# Reset state for the next day (to be called by the scheduler)
def reset_daily_state():
    global trade_count, atm_strike_price, straddle_positions, straddle_data_df
    print("Resetting daily state for new trading session.")
    trade_count = 0
    atm_strike_price = None
    straddle_positions = {'call': None, 'put': None}
    straddle_data_df = pd.DataFrame() # Clear the daily data
