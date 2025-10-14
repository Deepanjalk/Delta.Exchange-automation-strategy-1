import ccxt
import os
import pandas as pd
import pandas_ta as ta
import time
import logging
from datetime import datetime, timedelta
import pytz
from config import TIMEFRAME, SUPERTREND_LENGTH, SUPERTREND_MULTIPLIER

# --- Logger Setup ---
logger = logging.getLogger(__name__)

class TradingStrategy:
    def __init__(self):
        self.trade_count = 0
        self.atm_strike_price = None
        self.call_symbol = None
        self.put_symbol = None
        self.straddle_positions = {'call': None, 'put': None}
        self.straddle_data_df = pd.DataFrame()

    def _get_atm_strike_price(self, exchange, symbol):
        """
        Identifies the at-the-money (ATM) strike price for a given symbol.
        """
        try:
            ticker = exchange.fetch_ticker(symbol)
            underlying_price = ticker['last']
            logger.info(f"Underlying price for {symbol}: {underlying_price}")
            exchange.load_markets(True)
            markets = exchange.markets

            base_currency = 'BTC' # Explicitly define the base currency

            options = {
                s: m for s, m in markets.items()
                if m.get('option') and m.get('base') == base_currency and m.get('strike') is not None
            }
            if not options:
                logger.warning(f"No options with strike prices found for {base_currency}")
                return None

            logger.info(f"Found {len(options)} options for {base_currency}")

            closest_strike = None
            min_diff = float('inf')
            for s, m in options.items():
                strike = m.get('strike')
                if strike:
                    diff = abs(strike - underlying_price)
                    if diff < min_diff:
                        min_diff = diff
                        closest_strike = strike
            logger.info(f"ATM Strike Price: {closest_strike}")
            return closest_strike
        except ccxt.errors.ExchangeError as e:
            logger.error(f"An error occurred: {e}")
            return None

    def _get_historical_data(self, exchange, symbol, since=None, limit=200):
        """
        Fetches historical OHLCV data.
        """
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=limit)
            if not ohlcv:
                return pd.DataFrame()
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except ccxt.errors.ExchangeError as e:
            logger.error(f"An error occurred while fetching historical data for {symbol}: {e}")
            return None

    def _get_straddle_graph(self, exchange, call_symbol, put_symbol, limit=200):
        """
        Creates a straddle graph by combining the OHLC prices of a call and put option.
        """
        call_df = self._get_historical_data(exchange, call_symbol, limit)
        put_df = self._get_historical_data(exchange, put_symbol, limit)

        if call_df is None or put_df is None:
            return None

        combined_df = pd.merge(
            call_df,
            put_df,
            left_index=True,
            right_index=True,
            how='inner',
            suffixes=('_call', '_put')
        )

        straddle_df = pd.DataFrame(index=combined_df.index)
        straddle_df['close'] = combined_df['close_call'] + combined_df['close_put']
        straddle_df['high'] = combined_df['high_call'] + combined_df['high_put']
        straddle_df['low'] = combined_df['low_call'] + combined_df['low_put']

        return straddle_df

    def _get_supertrend(self, df):
        """
        Calculates the Supertrend indicator using settings from the config file.
        """
        if df is None or df.empty:
            return None
        df.ta.supertrend(length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER, append=True)

        # The column name from pandas-ta is dynamic.
        supertrend_direction_col = f'SUPERTd_{SUPERTREND_LENGTH}_{SUPERTREND_MULTIPLIER}'

        if supertrend_direction_col not in df.columns:
            logger.error(f"Supertrend direction column '{supertrend_direction_col}' not found. Available columns: {df.columns}")
            return None

        df.rename(columns={supertrend_direction_col: 'supertrend_direction'}, inplace=True)
        logger.info(f"Calculated Supertrend. Latest direction: {df.iloc[-1]['supertrend_direction']}")
        return df

    def _place_order(self, exchange, symbol, order_type, side, amount, price=None):
        """
        Places an order, retries on failure, and verifies its status.
        Returns the order object and a boolean indicating if it was filled.
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"Placing {side} {order_type} order for {amount} of {symbol} (Attempt {attempt + 1}/{max_retries})...")

                if order_type == 'limit':
                    order = exchange.create_order(symbol, order_type, side, amount, price)
                else:
                    order = exchange.create_order(symbol, order_type, side, amount)

                # Poll order status to confirm it is filled
                timeout_seconds = 60
                poll_interval_seconds = 5
                start_time = time.time()

                while time.time() - start_time < timeout_seconds:
                    try:
                        fetched_order = exchange.fetch_order(order['id'], symbol)
                        if fetched_order['status'] == 'closed':
                            logger.info(f"Order {order['id']} successfully filled.")
                            return fetched_order, True
                        elif fetched_order['status'] in ['canceled', 'rejected']:
                            logger.warning(f"Order {order['id']} was {fetched_order['status']}.")
                            return fetched_order, False # No retry if rejected/canceled

                        logger.info(f"Order {order['id']} status is {fetched_order['status']}. Retrying in {poll_interval_seconds}s...")
                        time.sleep(poll_interval_seconds)

                    except ccxt.errors.NetworkError as e:
                        logger.warning(f"Network error while fetching order status: {e}. Retrying...")
                        time.sleep(poll_interval_seconds)
                    except ccxt.errors.ExchangeError as e:
                        logger.error(f"Exchange error while fetching order status for {order['id']}: {e}")
                        break # Break polling loop on exchange error

                logger.warning(f"Order {order['id']} did not fill within {timeout_seconds} seconds.")
                # Attempt to cancel the lingering order before retrying
                try:
                    exchange.cancel_order(order['id'], symbol)
                    logger.info(f"Canceled lingering order {order['id']}.")
                except ccxt.errors.ExchangeError as cancel_e:
                    logger.error(f"Could not cancel lingering order {order['id']}: {cancel_e}")

            except ccxt.errors.ExchangeError as e:
                logger.error(f"An error occurred while placing order on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    logger.info("Retrying in 10 seconds...")
                    time.sleep(10)
                else:
                    logger.critical("Order placement failed after multiple retries. This is a critical error.")
                    return None, False
        return None, False

    def _determine_trade_action(self, supertrend_direction):
        """
        Determines the trade action based on the Supertrend signal and current positions.
        """
        is_call_open = self.straddle_positions.get('call') is not None
        is_put_open = self.straddle_positions.get('put') is not None

        if supertrend_direction == -1 and not is_call_open and not is_put_open:
            return 'CREATE_STRADDLE'
        elif supertrend_direction == 1 and is_call_open and is_put_open:
            return 'CLOSE_LOSING_LEG'
        elif supertrend_direction == -1 and (is_call_open ^ is_put_open):
            return 'RECONSTRUCT_STRADDLE'
        else:
            return 'HOLD'

    def _find_option_symbols(self, exchange, underlying_symbol, strike_price):
        """
        Finds the call and put option symbols for a given strike price with an expiry
        closest to the next day's 5:30 PM IST.
        """
        try:
            exchange.load_markets(True)
            markets = exchange.markets
        except (ccxt.errors.NetworkError, ccxt.errors.ExchangeError) as e:
            logger.error(f"Could not load markets from exchange: {e}")
            return None, None

        call_symbol, put_symbol = None, None

        ist = pytz.timezone('Asia/Kolkata')
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)

        # Target is 5:30 PM IST on the next day
        target_expiry_time_ist = (now_utc.astimezone(ist) + timedelta(days=1)).replace(hour=17, minute=30, second=0, microsecond=0)
        target_expiry_time_utc = target_expiry_time_ist.astimezone(pytz.utc)

        min_expiry_diff = timedelta(days=365) # Initialize with a large value

        # First pass to find the closest expiry date to our target
        closest_expiry_ts = None
        base_currency = 'BTC' # Explicitly define the base currency
        for symbol, market in markets.items():
            if (market.get('strike') == strike_price and
                market.get('base') == base_currency and
                market.get('expiry') is not None):

                expiry_ts = market.get('expiry')
                expiry_dt = datetime.fromtimestamp(expiry_ts / 1000, tz=pytz.utc)

                # We only care about options expiring in the future
                if expiry_dt > now_utc:
                    diff = abs(expiry_dt - target_expiry_time_utc)
                    if diff < min_expiry_diff:
                        min_expiry_diff = diff
                        closest_expiry_ts = expiry_ts

        if closest_expiry_ts is None:
            logger.warning(f"No future options found for strike {strike_price}.")
            return None, None

        # Second pass to get the symbols for the identified expiry date
        for symbol, market in markets.items():
            if (market.get('strike') == strike_price and
                market.get('base') == base_currency and
                market.get('expiry') == closest_expiry_ts):

                if market.get('optionType') == 'call':
                    call_symbol = symbol
                elif market.get('optionType') == 'put':
                    put_symbol = symbol

        return call_symbol, put_symbol

    def set_daily_atm_strike(self, exchange, symbol):
        """
        Identifies and sets the ATM strike price for the day.
        """
        logger.info("Identifying ATM strike price for the day...")
        self.atm_strike_price = self._get_atm_strike_price(exchange, symbol)
        if self.atm_strike_price is None:
            logger.warning("Could not determine ATM strike price. Trading will be paused.")

    def run_scheduled_strategy(self, exchange, symbol):
        """
        Runs the main trading strategy logic at scheduled intervals.
        """
        if self.atm_strike_price is None:
            logger.warning("ATM strike price not set for the day. Cannot run strategy.")
            return

        logger.info("\n" + "="*50)
        logger.info(f"Executing strategy run. Trade count: {self.trade_count}")

        if self.trade_count >= 3:
            logger.info("Maximum trade count reached for the day. Stopping.")
            return

        call_symbol, put_symbol = self._find_option_symbols(exchange, symbol, self.atm_strike_price)
        if not all([call_symbol, put_symbol]):
            logger.error(f"Could not find option symbols for strike {self.atm_strike_price}. Exiting.")
            return

        self.call_symbol = call_symbol
        self.put_symbol = put_symbol

        logger.info(f"Using ATM Call: {self.call_symbol}, ATM Put: {self.put_symbol}")

        # --- Unified Data Fetching Logic ---
        since_timestamp = None
        limit = 2  # By default, fetch latest 2 candles to be safe

        if self.straddle_data_df.empty:
            logger.info("First run for this session. Fetching all available candles...")
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)
            session_start_time = now_ist.replace(hour=17, minute=30, second=0, microsecond=0)

            # If current time is before 5:30PM, session started yesterday
            if now_ist.time() < session_start_time.time():
                session_start_time -= timedelta(days=1)

            since_timestamp = int(session_start_time.timestamp() * 1000)
            limit = 200  # Fetch up to 200 candles for the day

        call_df = self._get_historical_data(exchange, self.call_symbol, since=since_timestamp, limit=limit)
        put_df = self._get_historical_data(exchange, self.put_symbol, since=since_timestamp, limit=limit)

        if call_df.empty and self.straddle_data_df.empty:
            logger.warning("Could not fetch any candle data. Will try again on the next run.")
            return

        if not call_df.empty and not put_df.empty:
            # Combine the fetched data
            combined_df = pd.merge(
                call_df, put_df, left_index=True, right_index=True,
                how='inner', suffixes=('_call', '_put')
            )

            if not combined_df.empty:
                # Create straddle data from the combined data
                straddle_df_new = pd.DataFrame(index=combined_df.index)
                straddle_df_new['open'] = combined_df['open_call'] + combined_df['open_put']
                straddle_df_new['high'] = combined_df['high_call'] + combined_df['high_put']
                straddle_df_new['low'] = combined_df['low_call'] + combined_df['low_put']
                straddle_df_new['close'] = combined_df['close_call'] + combined_df['close_put']
                straddle_df_new['volume'] = combined_df['volume_call'] + combined_df['volume_put']

                # Update the main dataframe, avoiding duplicates
                new_candles = straddle_df_new[~straddle_df_new.index.isin(self.straddle_data_df.index)]
                if not new_candles.empty:
                    self.straddle_data_df = pd.concat([self.straddle_data_df, new_candles]).sort_index()
                    logger.info(f"Added {len(new_candles)} new candle(s) to the series.")

        logger.info(f"Total collected candles for the day: {len(self.straddle_data_df)}")

        if len(self.straddle_data_df) < SUPERTREND_LENGTH:
            logger.info("Not enough data to calculate Supertrend yet. Waiting for more candles.")
            return

        supertrend_df = self._get_supertrend(self.straddle_data_df.copy())

        if supertrend_df is None or supertrend_df.empty:
            logger.error("Could not calculate Supertrend. Exiting.")
            return

        latest_signal = supertrend_df.iloc[-1]
        supertrend_direction = latest_signal.get('supertrend_direction')

        action = self._determine_trade_action(supertrend_direction)

        logger.info(f"Supertrend Signal: {'BUY' if supertrend_direction == 1 else 'SELL' if supertrend_direction == -1 else 'NONE'}")
        logger.info(f"Current Positions: Call: {'Open' if self.straddle_positions['call'] else 'Closed'}, Put: {'Open' if self.straddle_positions['put'] else 'Closed'}")
        logger.info(f"Determined Action: {action}")

        if action == 'CREATE_STRADDLE':
            logger.info("Case A: Creating straddle.")
            call_order, call_filled = self._place_order(exchange, self.call_symbol, 'market', 'sell', 1)
            put_order, put_filled = self._place_order(exchange, self.put_symbol, 'market', 'sell', 1)

            if call_filled and put_filled:
                call_entry_price = call_order.get('average') or call_order.get('price')
                put_entry_price = put_order.get('average') or put_order.get('price')

                self.straddle_positions['call'] = {'order_id': call_order['id'], 'entry_price': call_entry_price}
                self.straddle_positions['put'] = {'order_id': put_order['id'], 'entry_price': put_entry_price}
                self.trade_count += 1
                logger.info(f"Straddle created. Call entry: {call_entry_price}, Put entry: {put_entry_price}")
            else:
                logger.critical("Failed to create complete straddle. One or both orders did not fill.")
                self._exit_all_positions(exchange)

        elif action == 'RECONSTRUCT_STRADDLE':
            logger.info("Case C: Reconstructing straddle.")
            if self.straddle_positions.get('call') is None:
                call_order, call_filled = self._place_order(exchange, self.call_symbol, 'market', 'sell', 1)
                if call_filled:
                    call_entry_price = call_order.get('average') or call_order.get('price')
                    self.straddle_positions['call'] = {'order_id': call_order['id'], 'entry_price': call_entry_price}
                    logger.info(f"Reconstructed straddle by selling call at {call_entry_price}.")
                else:
                    logger.critical("Failed to reconstruct straddle. Call order did not fill.")
                    self._exit_all_positions(exchange)

            elif self.straddle_positions.get('put') is None:
                put_order, put_filled = self._place_order(exchange, self.put_symbol, 'market', 'sell', 1)
                if put_filled:
                    put_entry_price = put_order.get('average') or put_order.get('price')
                    self.straddle_positions['put'] = {'order_id': put_order['id'], 'entry_price': put_entry_price}
                    logger.info(f"Reconstructed straddle by selling put at {put_entry_price}.")
                else:
                    logger.critical("Failed to reconstruct straddle. Put order did not fill.")
                    self._exit_all_positions(exchange)

        elif action == 'CLOSE_LOSING_LEG':
            logger.info("Case B: Closing the losing leg.")
            try:
                call_ticker = exchange.fetch_ticker(call_symbol)
                put_ticker = exchange.fetch_ticker(put_symbol)

                call_current_price = call_ticker['last']
                put_current_price = put_ticker['last']

                call_entry_price = self.straddle_positions['call']['entry_price']
                put_entry_price = self.straddle_positions['put']['entry_price']

                call_pnl = call_entry_price - call_current_price
                put_pnl = put_entry_price - put_current_price

                logger.info(f"Call PnL: {call_pnl:.4f}, Put PnL: {put_pnl:.4f}")

                if call_pnl < put_pnl:
                    logger.info("Call is the losing leg. Closing call position.")
                    close_order, order_filled = self._place_order(exchange, self.call_symbol, 'market', 'buy', 1)
                    if order_filled:
                        self.straddle_positions['call'] = None
                else:
                    logger.info("Put is the losing leg. Closing put position.")
                    close_order, order_filled = self._place_order(exchange, self.put_symbol, 'market', 'buy', 1)
                    if order_filled:
                        self.straddle_positions['put'] = None
            except (ccxt.errors.ExchangeError, KeyError) as e:
                logger.error(f"An error occurred while closing losing leg: {e}")

        elif action == 'HOLD':
            logger.info("Holding positions.")
        else:
            logger.info("No trade action taken.")

        logger.info(f"Current positions: {self.straddle_positions}")
        logger.info("="*50 + "\n")

    def _exit_all_positions(self, exchange):
        """
        A safety mechanism to close all open positions with market orders.
        """
        logger.warning("Executing safety exit for all open positions.")
        if self.straddle_positions.get('call'):
            logger.info(f"Closing open call position for {self.call_symbol}...")
            self._place_order(exchange, self.call_symbol, 'market', 'buy', 1)
            self.straddle_positions['call'] = None

        if self.straddle_positions.get('put'):
            logger.info(f"Closing open put position for {self.put_symbol}...")
            self._place_order(exchange, self.put_symbol, 'market', 'buy', 1)
            self.straddle_positions['put'] = None

        logger.critical("All positions have been closed due to a critical error. Halting further trades for the day.")
        # To prevent further trades, we can set trade_count to a high number
        self.trade_count = 99

    def reset_daily_state(self):
        logger.info("Resetting daily state for new trading session.")
        self.trade_count = 0
        self.atm_strike_price = None
        self.straddle_positions = {'call': None, 'put': None}
        self.straddle_data_df = pd.DataFrame()
