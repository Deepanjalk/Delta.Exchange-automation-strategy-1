import os
import pandas as pd
import pandas_ta as ta
import time
import logging
from datetime import datetime, timedelta
import pytz
from config import TIMEFRAME, SUPERTREND_LENGTH, SUPERTREND_MULTIPLIER, LOT_SIZE, LEVERAGE, MAX_POSITION_SIZE
from delta_api_client import DeltaExchangeAPI

# --- Logger Setup ---
logger = logging.getLogger(__name__)

class TradingStrategy:
    def __init__(self, api_client: DeltaExchangeAPI):
        self.api_client = api_client
        self.trade_count = 0
        self.atm_strike_price = None
        self.call_symbol = None
        self.put_symbol = None
        self.straddle_positions = {'call': None, 'put': None}
        self.straddle_data_df = pd.DataFrame()

    def _get_atm_strike_price(self, underlying_symbol: str):
        """
        Identifies the at-the-money (ATM) strike price for a given symbol.
        """
        try:
            self.atm_strike_price = self.api_client.get_atm_strike_price(underlying_symbol)
            if self.atm_strike_price:
                logger.info(f"ATM Strike Price: {self.atm_strike_price}")
            return self.atm_strike_price
        except Exception as e:
            logger.error(f"An error occurred getting ATM strike price: {e}")
            return None

    def _get_historical_data(self, symbol: str, hours_back: int = 24):
        """
        Fetches historical OHLCV data using Delta Exchange API.
        """
        try:
            data = self.api_client.get_option_historical_data(symbol, TIMEFRAME, hours_back)
            if not data:
                return pd.DataFrame()
            
            df = pd.DataFrame(data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"An error occurred while fetching historical data for {symbol}: {e}")
            return pd.DataFrame()

    def _get_straddle_graph(self, call_symbol: str, put_symbol: str, hours_back: int = 24):
        """
        Creates a straddle graph by combining the OHLC prices of a call and put option.
        A straddle is the sum of call and put option prices at the same strike and expiry.
        """
        try:
            straddle_data = self.api_client.create_straddle_data(call_symbol, put_symbol, TIMEFRAME, hours_back)
            if not straddle_data:
                return pd.DataFrame()
            
            df = pd.DataFrame(straddle_data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            # Ensure we have the required columns
            required_columns = ['open', 'high', 'low', 'close', 'volume']
            for col in required_columns:
                if col not in df.columns:
                    logger.warning(f"Missing column {col} in straddle data")
                    return pd.DataFrame()
            
            return df
        except Exception as e:
            logger.error(f"Error creating straddle graph: {e}")
            return pd.DataFrame()

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

    def _place_order(self, symbol: str, order_type: str, side: str, size: float = None, price: float = None):
        """
        Places an order using Delta Exchange API, retries on failure, and verifies its status.
        Returns the order object and a boolean indicating if it was filled.
        """
        # Use configured lot size if not specified
        if size is None:
            size = LOT_SIZE
            
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"Placing {side} {order_type} order for {size} lots of {symbol} (Attempt {attempt + 1}/{max_retries})...")

                # Place order with leverage consideration
                order = self.api_client.place_order(
                    symbol=symbol,
                    size=size,
                    side=side,
                    order_type=order_type,
                    price=price,
                    leverage=LEVERAGE if LEVERAGE > 1 else None
                )

                order_id = order['id']
                logger.info(f"Order placed with ID: {order_id}")

                # Poll order status to confirm it is filled
                timeout_seconds = 60
                poll_interval_seconds = 5
                start_time = time.time()

                while time.time() - start_time < timeout_seconds:
                    try:
                        fetched_order = self.api_client.get_order_by_id(order_id)
                        status = fetched_order['state']
                        
                        if status == 'filled':
                            logger.info(f"Order {order_id} successfully filled.")
                            return fetched_order, True
                        elif status in ['cancelled', 'rejected']:
                            logger.warning(f"Order {order_id} was {status}.")
                            return fetched_order, False  # No retry if rejected/canceled

                        logger.info(f"Order {order_id} status is {status}. Retrying in {poll_interval_seconds}s...")
                        time.sleep(poll_interval_seconds)

                    except Exception as e:
                        logger.warning(f"Error while fetching order status: {e}. Retrying...")
                        time.sleep(poll_interval_seconds)

                logger.warning(f"Order {order_id} did not fill within {timeout_seconds} seconds.")
                # Attempt to cancel the lingering order before retrying
                try:
                    self.api_client.cancel_order(order_id)
                    logger.info(f"Canceled lingering order {order_id}.")
                except Exception as cancel_e:
                    logger.error(f"Could not cancel lingering order {order_id}: {cancel_e}")

            except Exception as e:
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

    def _find_option_symbols(self, underlying_symbol: str, strike_price: float):
        """
        Finds the call and put option symbols for a given strike price with an expiry
        closest to the next day's 5:30 PM IST.
        """
        try:
            call_symbol, put_symbol = self.api_client.find_option_symbols(underlying_symbol, strike_price)
            return call_symbol, put_symbol
        except Exception as e:
            logger.error(f"Error finding option symbols: {e}")
            return None, None

    def set_daily_atm_strike(self, underlying_symbol: str):
        """
        Identifies and sets the ATM strike price for the day.
        """
        logger.info("Identifying ATM strike price for the day...")
        self.atm_strike_price = self._get_atm_strike_price(underlying_symbol)
        if self.atm_strike_price is None:
            logger.warning("Could not determine ATM strike price. Trading will be paused.")

    def run_scheduled_strategy(self, underlying_symbol: str):
        """
        Runs the main trading strategy logic at scheduled intervals.
        """
        if self.atm_strike_price is None:
            logger.warning("ATM strike price not set for the day. Cannot run strategy.")
            return

        logger.info("\n" + "="*50)
        logger.info(f"Executing strategy run. Trade count: {self.trade_count}")

        if self.trade_count >= MAX_POSITION_SIZE:
            logger.info(f"Maximum trade count ({MAX_POSITION_SIZE}) reached for the day. Stopping.")
            return

        call_symbol, put_symbol = self._find_option_symbols(underlying_symbol, self.atm_strike_price)
        if not all([call_symbol, put_symbol]):
            logger.error(f"Could not find option symbols for strike {self.atm_strike_price}. Exiting.")
            return

        self.call_symbol = call_symbol
        self.put_symbol = put_symbol

        logger.info(f"Using ATM Call: {self.call_symbol}, ATM Put: {self.put_symbol}")

        # Get straddle data
        straddle_df = self._get_straddle_graph(self.call_symbol, self.put_symbol)
        
        if straddle_df.empty:
            logger.warning("Could not fetch straddle data. Will try again on the next run.")
            return

        # Update the main dataframe
        if self.straddle_data_df.empty:
            self.straddle_data_df = straddle_df.copy()
        else:
            # Add new data, avoiding duplicates
            new_data = straddle_df[~straddle_df.index.isin(self.straddle_data_df.index)]
            if not new_data.empty:
                self.straddle_data_df = pd.concat([self.straddle_data_df, new_data]).sort_index()
                logger.info(f"Added {len(new_data)} new candle(s) to the series.")

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
            call_order, call_filled = self._place_order(self.call_symbol, 'market', 'sell', LOT_SIZE)
            put_order, put_filled = self._place_order(self.put_symbol, 'market', 'sell', LOT_SIZE)

            if call_filled and put_filled:
                call_entry_price = float(call_order.get('average_price', 0))
                put_entry_price = float(put_order.get('average_price', 0))

                self.straddle_positions['call'] = {'order_id': call_order['id'], 'entry_price': call_entry_price}
                self.straddle_positions['put'] = {'order_id': put_order['id'], 'entry_price': put_entry_price}
                self.trade_count += 1
                logger.info(f"Straddle created. Call entry: {call_entry_price}, Put entry: {put_entry_price}")
            else:
                logger.critical("Failed to create complete straddle. One or both orders did not fill.")
                self._exit_all_positions()

        elif action == 'RECONSTRUCT_STRADDLE':
            logger.info("Case C: Reconstructing straddle.")
            if self.straddle_positions.get('call') is None:
                call_order, call_filled = self._place_order(self.call_symbol, 'market', 'sell', LOT_SIZE)
                if call_filled:
                    call_entry_price = float(call_order.get('average_price', 0))
                    self.straddle_positions['call'] = {'order_id': call_order['id'], 'entry_price': call_entry_price}
                    logger.info(f"Reconstructed straddle by selling call at {call_entry_price}.")
                else:
                    logger.critical("Failed to reconstruct straddle. Call order did not fill.")
                    self._exit_all_positions()

            elif self.straddle_positions.get('put') is None:
                put_order, put_filled = self._place_order(self.put_symbol, 'market', 'sell', LOT_SIZE)
                if put_filled:
                    put_entry_price = float(put_order.get('average_price', 0))
                    self.straddle_positions['put'] = {'order_id': put_order['id'], 'entry_price': put_entry_price}
                    logger.info(f"Reconstructed straddle by selling put at {put_entry_price}.")
                else:
                    logger.critical("Failed to reconstruct straddle. Put order did not fill.")
                    self._exit_all_positions()

        elif action == 'CLOSE_LOSING_LEG':
            logger.info("Case B: Closing the losing leg.")
            try:
                # Get current prices for both options and underlying BTC
                call_ticker_response = self.api_client.get_ticker(self.call_symbol)
                put_ticker_response = self.api_client.get_ticker(self.put_symbol)
                btc_ticker_response = self.api_client.get_ticker('BTCUSD')

                # Validate ticker responses
                if not (call_ticker_response.get('success') and put_ticker_response.get('success') and btc_ticker_response.get('success')):
                    logger.error("Failed to retrieve one or more tickers. Cannot close losing leg.")
                    return

                call_ticker = call_ticker_response['result']
                put_ticker = put_ticker_response['result']
                btc_ticker = btc_ticker_response['result']

                # Use 'close' for option price, 'spot_price' for underlying
                call_current_price = float(call_ticker['close'])
                put_current_price = float(put_ticker['close'])
                btc_current_price = float(btc_ticker['spot_price'])

                call_entry_price = self.straddle_positions['call']['entry_price']
                put_entry_price = self.straddle_positions['put']['entry_price']

                # Calculate price changes (not PnL)
                call_price_change = call_current_price - call_entry_price
                put_price_change = put_current_price - put_entry_price

                logger.info(f"Call price change: {call_price_change:.4f}, Put price change: {put_price_change:.4f}")
                logger.info(f"BTC current price: {btc_current_price}")

                # Determine which leg is rising (losing for short straddle)
                # Rising leg = losing leg for short straddle
                if call_price_change > put_price_change:
                    logger.info("Call is the rising (losing) leg. Closing call position.")
                    close_order, order_filled = self._place_order(self.call_symbol, 'market', 'buy', LOT_SIZE)
                    if order_filled:
                        self.straddle_positions['call'] = None
                else:
                    logger.info("Put is the rising (losing) leg. Closing put position.")
                    close_order, order_filled = self._place_order(self.put_symbol, 'market', 'buy', LOT_SIZE)
                    if order_filled:
                        self.straddle_positions['put'] = None
            except Exception as e:
                logger.error(f"An error occurred while closing losing leg: {e}")

        elif action == 'HOLD':
            logger.info("Holding positions.")
        else:
            logger.info("No trade action taken.")

        logger.info(f"Current positions: {self.straddle_positions}")
        logger.info("="*50 + "\n")

    def _exit_all_positions(self):
        """
        A safety mechanism to close all open positions with market orders.
        """
        logger.warning("Executing safety exit for all open positions.")
        if self.straddle_positions.get('call'):
            logger.info(f"Closing open call position for {self.call_symbol}...")
            self._place_order(self.call_symbol, 'market', 'buy', LOT_SIZE)
            self.straddle_positions['call'] = None

        if self.straddle_positions.get('put'):
            logger.info(f"Closing open put position for {self.put_symbol}...")
            self._place_order(self.put_symbol, 'market', 'buy', LOT_SIZE)
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
