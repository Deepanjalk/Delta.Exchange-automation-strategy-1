
import ccxt
import os
import pandas as pd
from delta_strategy import get_atm_strike_price, find_option_symbols, get_straddle_graph, get_supertrend, determine_trade_action

def run_backtest():
    """
    Runs the backtesting simulation.
    """
    print("Starting backtest...")

    print("\n*** DISCLAIMER: BACKTESTING LIMITATIONS ***")
    print("This backtester simulates the strategy using the underlying asset's historical data.")
    print("It does NOT use historical options data, which is often unavailable.")
    print("Option prices are ESTIMATED, and therefore the results are a rough approximation.")
    print("Do not consider these results a reliable indicator of the strategy's future performance.")
    print("*******************************************\n")

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
    def fetch_historical_data_for_backtest(symbol, start_date, end_date, timeframe):
        since = exchange.parse8601(start_date + 'T00:00:00Z')
        end = exchange.parse8601(end_date + 'T23:59:59Z')
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
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df

    print("Fetching historical data...")
    underlying_df = fetch_historical_data_for_backtest(symbol, start_date, end_date, timeframe)

    if underlying_df.empty:
        print("No historical data found for the specified period.")
        return

    print("Historical data fetched successfully.")

    # --- Backtesting Loop ---
    balance = 1000  # Starting balance in USD
    positions = {'call': None, 'put': None}
    trade_log = []
    atm_strike_price = None

    for index, row in underlying_df.iterrows():
        if atm_strike_price is None or (trade_log and index.day != pd.to_datetime(trade_log[-1]['timestamp']).day):
             atm_strike_price = row['close']

        call_symbol, put_symbol = find_option_symbols(exchange, symbol, atm_strike_price)
        if not all([call_symbol, put_symbol]):
            continue

        straddle_df = pd.DataFrame([row])
        straddle_df = get_supertrend(straddle_df, supertrend_length, supertrend_multiplier)
        supertrend_direction = straddle_df.iloc[-1].get('supertrend_direction')

        action = determine_trade_action(supertrend_direction, positions)

        if action == 'CREATE_STRADDLE':
            positions['call'] = {'entry_price': row['close'] * 0.05, 'type': 'sell'}
            positions['put'] = {'entry_price': row['close'] * 0.05, 'type': 'sell'}
            trade_log.append({'timestamp': index, 'action': 'CREATE_STRADDLE', 'price': row['close']})

        elif action == 'CLOSE_GAINING_LEG':
            if positions['call'] and positions['put']:
                pnl = (positions['call']['entry_price'] - row['close'] * 0.03) + \
                      (positions['put']['entry_price'] - row['close'] * 0.03)
                balance += pnl
                trade_log.append({'timestamp': index, 'action': 'CLOSE_STRADDLE', 'pnl': pnl})
                positions = {'call': None, 'put': None}

    print("Backtest finished.")

    # --- Results ---
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
