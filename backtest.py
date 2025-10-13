
import ccxt
import os
import pandas as pd
from datetime import timedelta
from dotenv import load_dotenv
from delta_strategy import TradingStrategy
import logging

# --- Logger Setup ---
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

def run_backtest(start_date="2025-10-01", end_date="2025-10-07", timeframe='15m', supertrend_length=10, supertrend_multiplier=3):
    """
    Runs the backtesting simulation.
    """
    logger.info("Starting backtest...")
    logger.info(f"Configuration: Start={start_date}, End={end_date}, Timeframe={timeframe}, Supertrend={supertrend_length},{supertrend_multiplier}")

    symbol = 'BTC/USD'
    strategy = TradingStrategy()

    # --- Exchange Setup ---
    exchange_id = 'delta'
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'apiKey': os.environ.get('DELTA_API_KEY'),
        'secret': os.environ.get('DELTA_API_SECRET'),
        'urls': {
            'api': {
                'public': 'https://api.india.delta.exchange',
                'private': 'https://api.india.delta.exchange',
            },
        },
    })

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
                logger.error(f"Error fetching historical data: {e}")
                break
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
        return df

    balance = 1000
    trade_log = []

    def get_closest_strike(underlying_price, options):
        if not options: return None
        return min(options, key=lambda s: abs(s['strike'] - underlying_price))['strike']

    def find_option_symbols_for_backtest(markets, underlying_symbol, strike_price, current_date):
        call_symbol, put_symbol = None, None
        min_expiry_diff = timedelta(days=100)

        for symbol, market in markets.items():
            if market.get('strike') == strike_price and market.get('base') == underlying_symbol.split('/')[0]:
                expiry = pd.to_datetime(market.get('expiry'), unit='ms')
                if expiry > current_date:
                    diff = expiry - current_date
                    if diff < min_expiry_diff:
                        min_expiry_diff = diff
                        call_symbol, put_symbol = (None, None)

        for symbol, market in markets.items():
            if market.get('strike') == strike_price and market.get('base') == underlying_symbol.split('/')[0]:
                expiry = pd.to_datetime(market.get('expiry'), unit='ms')
                if expiry > current_date and (expiry - current_date) == min_expiry_diff:
                    if market.get('optionType') == 'call': call_symbol = symbol
                    elif market.get('optionType') == 'put': put_symbol = symbol

        return call_symbol, put_symbol

    date_range = pd.to_datetime(pd.date_range(start=start_date, end=end_date))

    for current_date in date_range:
        strategy.reset_daily_state()
        current_date_str = current_date.strftime('%Y-%m-%d')
        logger.info(f"\n--- Processing {current_date_str} ---")

        exchange.load_markets(True)
        markets = exchange.markets
        options = [m for s, m in markets.items() if m.get('option') and m.get('base') == symbol.split('/')[0]]

        underlying_df_daily = fetch_historical_data_for_backtest(symbol, current_date_str, '1d')
        if underlying_df_daily.empty:
            logger.warning(f"No underlying data for {current_date_str}, skipping.")
            continue

        underlying_price_eod = underlying_df_daily.iloc[0]['close']
        atm_strike_price = get_closest_strike(underlying_price_eod, options)

        if atm_strike_price is None:
            logger.warning(f"Could not find ATM strike for underlying price {underlying_price_eod}")
            continue

        logger.info(f"Underlying Price: {underlying_price_eod}, ATM Strike Price: {atm_strike_price}")

        call_symbol, put_symbol = find_option_symbols_for_backtest(markets, symbol, atm_strike_price, current_date)
        if not all([call_symbol, put_symbol]):
            logger.warning(f"Could not find option symbols for strike {atm_strike_price}.")
            continue

        logger.info(f"Using Call: {call_symbol}, Put: {put_symbol}")

        call_df = fetch_historical_data_for_backtest(call_symbol, current_date_str, timeframe)
        put_df = fetch_historical_data_for_backtest(put_symbol, current_date_str, timeframe)

        if call_df.empty or put_df.empty:
            logger.warning(f"No historical options data for {current_date_str}.")
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

        for index, row in straddle_df.iterrows():
            strategy.straddle_data_df = pd.concat([strategy.straddle_data_df, row.to_frame().T])

            if len(strategy.straddle_data_df) < supertrend_length:
                continue

            supertrend_df = strategy._get_supertrend(strategy.straddle_data_df.copy(), supertrend_length, supertrend_multiplier)
            if supertrend_df is None or supertrend_df.empty:
                continue

            latest_signal = supertrend_df.iloc[-1]
            supertrend_direction = latest_signal.get('supertrend_direction')
            action = strategy._determine_trade_action(supertrend_direction)

            if action == 'CREATE_STRADDLE' and strategy.trade_count < 3:
                strategy.straddle_positions['call'] = {'entry_price': row['close_call'], 'type': 'sell'}
                strategy.straddle_positions['put'] = {'entry_price': row['close_put'], 'type': 'sell'}
                strategy.trade_count += 1
                trade_log.append({
                    'timestamp': index, 'action': 'CREATE_STRADDLE',
                    'call_entry': row['close_call'], 'put_entry': row['close_put'],
                    'trade_of_day': strategy.trade_count
                })

            elif action == 'CLOSE_LOSING_LEG':
                if strategy.straddle_positions['call'] and strategy.straddle_positions['put']:
                    call_pnl = strategy.straddle_positions['call']['entry_price'] - row['close_call']
                    put_pnl = strategy.straddle_positions['put']['entry_price'] - row['close_put']

                    if call_pnl < put_pnl:
                        balance += call_pnl
                        trade_log.append({'timestamp': index, 'action': 'CLOSE_CALL_LEG', 'pnl': call_pnl})
                        strategy.straddle_positions['call'] = None
                    else:
                        balance += put_pnl
                        trade_log.append({'timestamp': index, 'action': 'CLOSE_PUT_LEG', 'pnl': put_pnl})
                        strategy.straddle_positions['put'] = None

            elif action == 'RECONSTRUCT_STRADDLE':
                if strategy.straddle_positions.get('call') is None:
                    strategy.straddle_positions['call'] = {'entry_price': row['close_call'], 'type': 'sell'}
                    trade_log.append({'timestamp': index, 'action': 'RECONSTRUCT_SELL_CALL', 'price': row['close_call']})
                if strategy.straddle_positions.get('put') is None:
                    strategy.straddle_positions['put'] = {'entry_price': row['close_put'], 'type': 'sell'}
                    trade_log.append({'timestamp': index, 'action': 'RECONSTRUCT_SELL_PUT', 'price': row['close_put']})

    logger.info("Backtest finished.")
    total_trades = len([t for t in trade_log if t['action'] == 'CREATE_STRADDLE'])
    winning_trades = len([t for t in trade_log if t.get('pnl', 0) > 0])
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0

    logger.info("\n--- Backtesting Results ---")
    logger.info(f"Starting Balance: 1000 USD")
    logger.info(f"Final Balance: {balance:.2f} USD")
    logger.info(f"Total Trades: {total_trades}")
    logger.info(f"Winning Trades: {winning_trades}")
    logger.info(f"Win Rate: {win_rate:.2f}%")
    logger.info("\nTrade Log:")
    for trade in trade_log:
        logger.info(trade)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
    run_backtest()
