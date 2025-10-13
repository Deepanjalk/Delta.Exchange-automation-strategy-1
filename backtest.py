
import ccxt
import os
import pandas as pd
from datetime import timedelta
from delta_strategy import get_supertrend, determine_trade_action

def run_backtest():
    """
    Runs the backtesting simulation.
    """
    print("Starting backtest...")

    # --- User Input ---
    start_date = input("Enter start date (YYYY-MM-DD): ")
    end_date = input("Enter end date (YYYY-MM-DD): ")
    timeframe = input("Enter timeframe for straddle graph (e.g., '15m', '1h'): ")
    supertrend_length = int(input("Enter Supertrend length: "))
    supertrend_multiplier = float(input("Enter Supertrend multiplier: "))
    symbol = 'BTC/USDT'

    # --- Exchange Setup ---
    exchange_id = 'delta'
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'apiKey': os.environ.get('DELTA_API_KEY'),
        'secret': os.environ.get('DELTA_API_SECRET'),
        'urls': {
            'api': 'https://api.india.delta.exchange',
        },
    })
    exchange.set_sandbox_mode(True)

    # --- Historical Data Fetching ---
    def fetch_historical_data_for_backtest(symbol, date, timeframe):
        since = exchange.parse8601(date + 'T00:00:00Z')
        end = exchange.parse8601(date + 'T23:59:59Z')
        all_ohlcv = []
        while since < end:
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + exchange.parse_timeframe(timeframe) * 1000
            except Exception as e:
                print(f"Error fetching historical data: {e}")
                break
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
        return df

    # --- Backtesting Loop ---
    balance = 1000  # Starting balance in USD
    positions = {'call': None, 'put': None}
    trade_log = []

    def get_closest_strike(underlying_price, options):
        if not options:
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
        return closest_strike

    def find_option_symbols_for_backtest(markets, underlying_symbol, strike_price, current_date):
        call_symbol, put_symbol = None, None
        min_expiry_diff = float('inf')

        for symbol, market in markets.items():
            if market.get('strike') == strike_price and market.get('base') == underlying_symbol.split('/')[0]:
                expiry = pd.to_datetime(market.get('expiry'), unit='ms')
                if expiry > current_date:
                    diff = expiry - current_date
                    if diff < timedelta(days=7): # Weekly options
                        if diff < min_expiry_diff:
                            min_expiry_diff = diff
                            call_symbol = symbol if market.get('optionType') == 'call' else call_symbol
                            put_symbol = symbol if market.get('optionType') == 'put' else put_symbol

        return call_symbol, put_symbol


    date_range = pd.to_datetime(pd.date_range(start=start_date, end=end_date))

    for current_date in date_range:
        current_date_str = current_date.strftime('%Y-%m-%d')
        print(f"\n--- Processing {current_date_str} ---")

        exchange.load_markets(True)
        markets = exchange.markets
        options = {
            s: m for s, m in markets.items()
            if m.get('option') and m.get('base') == symbol.split('/')[0]
        }

        underlying_df_daily = fetch_historical_data_for_backtest(symbol, current_date_str, '1d')
        if underlying_df_daily.empty:
            print(f"No underlying data for {current_date_str}, skipping.")
            continue

        underlying_price_eod = underlying_df_daily.iloc[0]['close']
        atm_strike_price = get_closest_strike(underlying_price_eod, options)

        if atm_strike_price is None:
            print(f"Could not find ATM strike for underlying price {underlying_price_eod}")
            continue

        print(f"Underlying Price: {underlying_price_eod}, ATM Strike Price: {atm_strike_price}")

        call_symbol, put_symbol = find_option_symbols_for_backtest(markets, symbol, atm_strike_price, current_date)
        if not all([call_symbol, put_symbol]):
            print(f"Could not find option symbols for strike {atm_strike_price}.")
            continue

        print(f"Using Call: {call_symbol}, Put: {put_symbol}")

        call_df = fetch_historical_data_for_backtest(call_symbol, current_date_str, timeframe)
        put_df = fetch_historical_data_for_backtest(put_symbol, current_date_str, timeframe)

        if call_df.empty or put_df.empty:
            print(f"No historical options data for {current_date_str}.")
            continue

        straddle_df = pd.merge(
            call_df.add_suffix('_call'),
            put_df.add_suffix('_put'),
            left_index=True,
            right_index=True,
            how='inner'
        )
        straddle_df['close'] = straddle_df['close_call'] + straddle_df['close_put']
        straddle_df['high'] = straddle_df['high_call'] + straddle_df['high_put']
        straddle_df['low'] = straddle_df['low_call'] + straddle_df['low_put']

        straddle_df = get_supertrend(straddle_df, supertrend_length, supertrend_multiplier)

        for index, row in straddle_df.iterrows():
            supertrend_direction = row.get('supertrend_direction')
            action = determine_trade_action(supertrend_direction, positions)

            if action == 'CREATE_STRADDLE':
                positions['call'] = {'entry_price': row['close_call'], 'type': 'sell'}
                positions['put'] = {'entry_price': row['close_put'], 'type': 'sell'}
                trade_log.append({
                    'timestamp': index,
                    'action': 'CREATE_STRADDLE',
                    'call_entry': row['close_call'],
                    'put_entry': row['close_put']
                })

            elif action == 'CLOSE_GAINING_LEG':
                if positions['call'] and positions['put']:
                    call_pnl = positions['call']['entry_price'] - row['close_call']
                    put_pnl = positions['put']['entry_price'] - row['close_put']
                    pnl = call_pnl + put_pnl
                    balance += pnl
                    trade_log.append({
                        'timestamp': index,
                        'action': 'CLOSE_STRADDLE',
                        'pnl': pnl
                    })
                    positions = {'call': None, 'put': None}

    print("Backtest finished.")

    total_trades = len([t for t in trade_log if t['action'] == 'CREATE_STRADDLE'])
    winning_trades = len([t for t in trade_log if t.get('pnl', 0) > 0])
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0

    print("\n--- Backtesting Results ---")
    print(f"Starting Balance: 1000 USD")
    print(f"Final Balance: {balance:.2f} USD")
    print(f"Total Trades: {total_trades}")
    print(f"Winning Trades: {winning_trades}")
    print(f"Win Rate: {win_rate:.2f}%")
    print("\nTrade Log:")
    for trade in trade_log:
        print(trade)

if __name__ == '__main__':
    run_backtest()
