
import ccxt
import os
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from delta_strategy import TradingStrategy
import logging
import pytz

# --- Logger Setup ---
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

def run_backtest(start_date="2025-10-13", timeframe='15m', supertrend_length=10, supertrend_multiplier=3):
    """
    Runs a single-day backtesting simulation based on the strategy's constraints.
    """
    logger.info("Starting backtest simulation for a single trading session...")
    logger.info(f"Configuration: Date={start_date}, Timeframe={timeframe}, Supertrend={supertrend_length},{supertrend_multiplier}")

    symbol = 'BTC/USD'
    strategy = TradingStrategy()

    # --- Exchange Setup ---
    exchange = ccxt.delta({
        'apiKey': os.environ.get('DELTA_API_KEY'),
        'secret': os.environ.get('DELTA_API_SECRET'),
        'urls': {
            'api': {
                'public': 'https://api.india.delta.exchange',
                'private': 'https://api.india.delta.exchange',
            },
        },
    })

    # --- Simulation Settings ---
    balance = 1000
    trade_log = []
    TRANSACTION_FEE_RATE = 0.0005
    SLIPPAGE_PERCENT = 0.0002

    # --- Main Simulation Loop ---
    strategy.reset_daily_state()

    # Set the start time for the simulation (5:30 PM IST)
    ist = pytz.timezone('Asia/Kolkata')
    start_dt_ist = datetime.strptime(start_date, '%Y-%m-%d').replace(hour=17, minute=30, tzinfo=ist)
    start_ts = int(start_dt_ist.timestamp() * 1000)

    logger.info(f"--- Processing single session starting from {start_dt_ist.strftime('%Y-%m-%d %H:%M:%S IST')} ---")

    # Determine ATM strike at the beginning of the session
    try:
        ticker = exchange.fetch_ticker(symbol)
        underlying_price = ticker['last']
        strategy.atm_strike_price = strategy._get_atm_strike_price(exchange, symbol)
    except (ccxt.errors.NetworkError, ccxt.errors.ExchangeError) as e:
        logger.error(f"Failed to fetch initial data: {e}")
        return

    if strategy.atm_strike_price is None:
        logger.error("Could not determine ATM strike price. Aborting backtest.")
        return

    logger.info(f"Initial Underlying Price: {underlying_price}, ATM Strike Price: {strategy.atm_strike_price}")

    call_symbol, put_symbol = strategy._find_option_symbols(exchange, symbol, strategy.atm_strike_price)
    if not all([call_symbol, put_symbol]):
        logger.error(f"Could not find option symbols for strike {strategy.atm_strike_price}. Aborting backtest.")
        return

    logger.info(f"Using Call: {call_symbol}, Put: {put_symbol}")

    # Simulate candle-by-candle processing
    timeframe_duration_ms = exchange.parse_timeframe(timeframe) * 1000
    for i in range(200): # Simulate for a reasonable number of candles
        since = start_ts + i * timeframe_duration_ms

        try:
            call_ohlcv = exchange.fetch_ohlcv(call_symbol, timeframe, since, limit=1)
            put_ohlcv = exchange.fetch_ohlcv(put_symbol, timeframe, since, limit=1)
        except Exception as e:
            logger.error(f"Error fetching candle data: {e}")
            continue

        if not call_ohlcv or not put_ohlcv:
            logger.warning("No more candle data available for this session.")
            break

        call_candle = call_ohlcv[0]
        put_candle = put_ohlcv[0]

        timestamp = pd.to_datetime(call_candle[0], unit='ms')

        row_data = {
            'open_call': call_candle[1], 'high_call': call_candle[2], 'low_call': call_candle[3], 'close_call': call_candle[4],
            'open_put': put_candle[1], 'high_put': put_candle[2], 'low_put': put_candle[3], 'close_put': put_candle[4],
            'close': call_candle[4] + put_candle[4],
            'high': call_candle[2] + put_candle[2],
            'low': call_candle[3] + put_candle[3]
        }

        new_row = pd.DataFrame(row_data, index=[timestamp])
        strategy.straddle_data_df = pd.concat([strategy.straddle_data_df, new_row])

        if len(strategy.straddle_data_df) < supertrend_length + 2:
            continue

        strategy.straddle_data_df = strategy._get_supertrend(strategy.straddle_data_df.copy())
        if strategy.straddle_data_df is None:
            continue

        latest_signal = strategy.straddle_data_df.iloc[-1]
        supertrend_direction = latest_signal.get('supertrend_direction')
        action = strategy._determine_trade_action(supertrend_direction)

        if action == 'CREATE_STRADDLE' and strategy.trade_count < 3:
            call_entry_price = row_data['close_call'] * (1 - SLIPPAGE_PERCENT)
            put_entry_price = row_data['close_put'] * (1 - SLIPPAGE_PERCENT)

            balance -= (call_entry_price * TRANSACTION_FEE_RATE) + (put_entry_price * TRANSACTION_FEE_RATE)

            strategy.straddle_positions['call'] = {'entry_price': call_entry_price, 'type': 'sell'}
            strategy.straddle_positions['put'] = {'entry_price': put_entry_price, 'type': 'sell'}
            strategy.trade_count += 1
            trade_log.append({
                'timestamp': timestamp, 'action': 'CREATE_STRADDLE',
                'call_entry': call_entry_price, 'put_entry': put_entry_price,
                'trade_of_day': strategy.trade_count,
                'balance': balance
            })

        elif action == 'CLOSE_LOSING_LEG':
            if strategy.straddle_positions['call'] and strategy.straddle_positions['put']:
                call_exit_price = row_data['close_call'] * (1 + SLIPPAGE_PERCENT)
                put_exit_price = row_data['close_put'] * (1 + SLIPPAGE_PERCENT)

                call_pnl = strategy.straddle_positions['call']['entry_price'] - call_exit_price
                put_pnl = strategy.straddle_positions['put']['entry_price'] - put_exit_price

                if call_pnl < put_pnl: # Call is the losing leg
                    balance += call_pnl - (call_exit_price * TRANSACTION_FEE_RATE)
                    trade_log.append({'timestamp': timestamp, 'action': 'CLOSE_CALL_LEG', 'pnl': call_pnl, 'balance': balance})
                    strategy.straddle_positions['call'] = None
                else: # Put is the losing leg
                    balance += put_pnl - (put_exit_price * TRANSACTION_FEE_RATE)
                    trade_log.append({'timestamp': timestamp, 'action': 'CLOSE_PUT_LEG', 'pnl': put_pnl, 'balance': balance})
                    strategy.straddle_positions['put'] = None

        elif action == 'RECONSTRUCT_STRADDLE':
            if strategy.straddle_positions.get('call') is None:
                entry_price = row_data['close_call'] * (1 - SLIPPAGE_PERCENT)
                balance -= entry_price * TRANSACTION_FEE_RATE
                strategy.straddle_positions['call'] = {'entry_price': entry_price, 'type': 'sell'}
                trade_log.append({'timestamp': timestamp, 'action': 'RECONSTRUCT_SELL_CALL', 'price': entry_price, 'balance': balance})

            if strategy.straddle_positions.get('put') is None:
                entry_price = row_data['close_put'] * (1 - SLIPPAGE_PERCENT)
                balance -= entry_price * TRANSACTION_FEE_RATE
                strategy.straddle_positions['put'] = {'entry_price': entry_price, 'type': 'sell'}
                trade_log.append({'timestamp': timestamp, 'action': 'RECONSTRUCT_SELL_PUT', 'price': entry_price, 'balance': balance})

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
