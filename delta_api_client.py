import hmac
import hashlib
import time
import requests
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

class DeltaExchangeAPI:
    """
    Native Delta Exchange API client implementing HMAC authentication
    and all required endpoints according to docs.delta.exchange
    """
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.delta.exchange"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        
    def _generate_signature(self, method: str, path: str, query_string: str, body: str, timestamp: str) -> str:
        """
        Generate HMAC signature for Delta Exchange API authentication
        """
        message = f"{method}{path}{query_string}{body}{timestamp}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method: str, endpoint: str, params: Dict = None, data: Dict = None) -> Dict:
        """
        Make authenticated request to Delta Exchange API
        """
        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        
        # Prepare query string for signature
        query_string = ""
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        
        # Prepare body for signature
        body = ""
        if data:
            body = json.dumps(data, separators=(',', ':'))
        
        # Generate signature
        signature = self._generate_signature(method, endpoint, query_string, body, timestamp)
        
        # Prepare headers
        headers = {
            'api-key': self.api_key,
            'timestamp': timestamp,
            'signature': signature,
            'Content-Type': 'application/json'
        }
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=params, headers=headers)
            elif method == 'POST':
                response = self.session.post(url, json=data, params=params, headers=headers)
            elif method == 'PUT':
                response = self.session.put(url, json=data, params=params, headers=headers)
            elif method == 'DELETE':
                response = self.session.delete(url, params=params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"Error response: {error_data}")
                except:
                    logger.error(f"Error response text: {e.response.text}")
            raise
    
    # Public API Methods
    
    def get_products(self, symbol: str = None) -> List[Dict]:
        """
        Get list of products (derivatives contracts)
        """
        params = {}
        if symbol:
            params['symbol'] = symbol
        return self._make_request('GET', '/v2/products', params=params)
    
    def get_product_by_symbol(self, symbol: str) -> Dict:
        """
        Get product by symbol
        """
        return self._make_request('GET', f'/v2/products/{symbol}')
    
    def get_tickers(self, symbols: List[str] = None) -> List[Dict]:
        """
        Get tickers for products
        """
        params = {}
        if symbols:
            params['symbols'] = ','.join(symbols)
        return self._make_request('GET', '/v2/tickers', params=params)
    
    def get_ticker(self, symbol: str) -> Dict:
        """
        Get ticker for a specific product
        """
        return self._make_request('GET', f'/v2/tickers/{symbol}')
    
    def get_option_chain(self, underlying_asset: str) -> List[Dict]:
        """
        Get option chain for underlying asset
        """
        params = {'underlying_asset': underlying_asset}
        return self._make_request('GET', '/v2/option_chain', params=params)
    
    def get_historical_ohlc(self, symbol: str, resolution: str, start_time: int, end_time: int) -> List[Dict]:
        """
        Get historical OHLC candles
        """
        params = {
            'symbol': symbol,
            'resolution': resolution,
            'start': start_time,
            'end': end_time
        }
        return self._make_request('GET', '/v2/history/candles', params=params)
    
    def get_l2_orderbook(self, symbol: str) -> Dict:
        """
        Get L2 orderbook
        """
        return self._make_request('GET', f'/v2/l2orderbook/{symbol}')
    
    def get_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        """
        Get public trades
        """
        params = {'symbol': symbol, 'limit': limit}
        return self._make_request('GET', '/v2/trades', params=params)
    
    # Private API Methods
    
    def get_wallet_balances(self) -> List[Dict]:
        """
        Get wallet balances
        """
        return self._make_request('GET', '/v2/wallet/balances')
    
    def get_positions(self) -> List[Dict]:
        """
        Get margined positions
        """
        return self._make_request('GET', '/v2/positions')
    
    def get_position(self, symbol: str) -> Dict:
        """
        Get specific position
        """
        return self._make_request('GET', f'/v2/positions/{symbol}')
    
    def get_active_orders(self, symbol: str = None) -> List[Dict]:
        """
        Get active orders
        """
        params = {}
        if symbol:
            params['symbol'] = symbol
        return self._make_request('GET', '/v2/orders', params=params)
    
    def get_order_by_id(self, order_id: str) -> Dict:
        """
        Get order by ID
        """
        return self._make_request('GET', f'/v2/orders/{order_id}')
    
    def place_order(self, symbol: str, size: float, side: str, order_type: str, 
                   price: float = None, time_in_force: str = 'GTC', 
                   reduce_only: bool = False, post_only: bool = False,
                   leverage: float = None) -> Dict:
        """
        Place order
        """
        data = {
            'product_id': symbol,  # Delta Exchange uses product_id
            'size': str(size),
            'side': side,
            'order_type': order_type,
            'time_in_force': time_in_force,
            'reduce_only': reduce_only,
            'post_only': post_only
        }
        
        if price is not None:
            data['price'] = str(price)
        
        if leverage is not None:
            data['leverage'] = str(leverage)
        
        return self._make_request('POST', '/v2/orders', data=data)
    
    def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel order
        """
        return self._make_request('DELETE', f'/v2/orders/{order_id}')
    
    def cancel_all_orders(self, symbol: str = None) -> Dict:
        """
        Cancel all open orders
        """
        data = {}
        if symbol:
            data['symbol'] = symbol
        return self._make_request('DELETE', '/v2/orders/cancel_all', data=data)
    
    def edit_order(self, order_id: str, size: float = None, price: float = None) -> Dict:
        """
        Edit order
        """
        data = {}
        if size is not None:
            data['size'] = str(size)
        if price is not None:
            data['price'] = str(price)
        
        return self._make_request('PUT', f'/v2/orders/{order_id}', data=data)
    
    def get_order_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        Get order history
        """
        params = {'limit': limit}
        if symbol:
            params['symbol'] = symbol
        return self._make_request('GET', '/v2/orders/history', params=params)
    
    def get_user_fills(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        Get user fills
        """
        params = {'limit': limit}
        if symbol:
            params['symbol'] = symbol
        return self._make_request('GET', '/v2/fills', params=params)
    
    def close_position(self, symbol: str) -> Dict:
        """
        Close position
        """
        return self._make_request('POST', f'/v2/positions/{symbol}/close')
    
    def close_all_positions(self) -> Dict:
        """
        Close all positions
        """
        return self._make_request('POST', '/v2/positions/close_all')
    
    def add_position_margin(self, symbol: str, amount: float) -> Dict:
        """
        Add position margin
        """
        data = {'amount': str(amount)}
        return self._make_request('POST', f'/v2/positions/{symbol}/margin', data=data)
    
    def remove_position_margin(self, symbol: str, amount: float) -> Dict:
        """
        Remove position margin
        """
        data = {'amount': str(amount)}
        return self._make_request('DELETE', f'/v2/positions/{symbol}/margin', data=data)
    
    def get_user_preferences(self) -> Dict:
        """
        Get user trading preferences
        """
        return self._make_request('GET', '/v2/user/preferences')
    
    def update_user_preferences(self, preferences: Dict) -> Dict:
        """
        Update user trading preferences
        """
        return self._make_request('PUT', '/v2/user/preferences', data=preferences)
    
    def get_user(self) -> Dict:
        """
        Get user information
        """
        return self._make_request('GET', '/v2/user')
    
    def get_subaccounts(self) -> List[Dict]:
        """
        Get subaccounts
        """
        return self._make_request('GET', '/v2/subaccounts')
    
    def get_assets(self) -> List[Dict]:
        """
        Get list of all assets
        """
        return self._make_request('GET', '/v2/assets')
    
    def get_indices(self) -> List[Dict]:
        """
        Get indices
        """
        return self._make_request('GET', '/v2/indices')
    
    def get_settlement_prices(self, symbol: str = None) -> List[Dict]:
        """
        Get settlement prices
        """
        params = {}
        if symbol:
            params['symbol'] = symbol
        return self._make_request('GET', '/v2/settlement_prices', params=params)
    
    def get_volume_stats(self) -> List[Dict]:
        """
        Get volume stats
        """
        return self._make_request('GET', '/v2/stats')
    
    def get_order_leverage(self, product_id: str) -> Dict:
        """
        Get order leverage for a specific product
        """
        return self._make_request('GET', f'/v2/products/{product_id}/orders/leverage')
    
    def get_available_leverage(self, product_id: str) -> Optional[float]:
        """
        Get available leverage for a product
        """
        try:
            response = self.get_order_leverage(product_id)
            if response.get('success') and 'result' in response:
                return float(response['result']['leverage'])
            return None
        except Exception as e:
            logger.error(f"Error getting leverage for product {product_id}: {e}")
            return None
    
    # Helper methods for trading strategy
    
    def get_atm_strike_price(self, underlying_symbol: str) -> Optional[float]:
        """
        Get ATM strike price for options trading
        """
        try:
            # Get current price of underlying
            ticker = self.get_ticker(underlying_symbol)
            if not ticker.get('success', False):
                logger.error(f"Failed to get ticker for {underlying_symbol}: {ticker}")
                return None

            current_price = float(ticker['result']['spot_price'])
            
            # Get option chain
            # The API expects the base asset symbol for option chain, e.g., 'BTC' from 'BTCUSD'
            base_asset = underlying_symbol.replace('USD', '')
            option_chain = self.get_option_chain(base_asset)
            
            if not option_chain:
                logger.warning(f"No options found for {underlying_symbol}")
                return None
            
            # Find closest strike to current price
            closest_strike = None
            min_diff = float('inf')
            
            for option in option_chain:
                strike = float(option['strike_price'])
                diff = abs(strike - current_price)
                if diff < min_diff:
                    min_diff = diff
                    closest_strike = strike
            
            logger.info(f"Current price: {current_price}, ATM strike: {closest_strike}")
            return closest_strike
            
        except Exception as e:
            logger.error(f"Error getting ATM strike price: {e}")
            return None
    
    def find_option_symbols(self, underlying_symbol: str, strike_price: float, 
                           target_expiry: datetime = None) -> tuple:
        """
        Find call and put option symbols for given strike and expiry
        """
        try:
            base_asset = underlying_symbol.replace('USD', '')
            option_chain = self.get_option_chain(base_asset)
            
            if not option_chain:
                logger.error(f"No option chain found for {base_asset}")
                return None, None
            
            call_symbol = None
            put_symbol = None
            
            # If no target expiry provided, find closest to next day 5:30 PM IST
            if target_expiry is None:
                ist = pytz.timezone('Asia/Kolkata')
                now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
                target_expiry = (now_utc.astimezone(ist) + timedelta(days=1)).replace(
                    hour=17, minute=30, second=0, microsecond=0
                ).astimezone(pytz.utc)
            
            # Find options with matching strike and closest expiry
            matching_options = []
            for option in option_chain:
                if float(option['strike_price']) == strike_price:
                    expiry_dt = datetime.fromisoformat(option['expiry'].replace('Z', '+00:00'))
                    if expiry_dt > datetime.utcnow().replace(tzinfo=pytz.utc):
                        matching_options.append((option, expiry_dt))
            
            if not matching_options:
                logger.warning(f"No future options found for strike {strike_price}")
                return None, None
            
            # Find closest expiry to target
            closest_option = min(matching_options, 
                               key=lambda x: abs((x[1] - target_expiry).total_seconds()))
            
            expiry_dt = closest_option[1]
            options_for_expiry = [opt for opt, exp in matching_options if exp == expiry_dt]
            
            # Find call and put for this expiry
            for option in options_for_expiry:
                if option['option_type'] == 'call':
                    call_symbol = option['symbol']
                elif option['option_type'] == 'put':
                    put_symbol = option['symbol']
            
            logger.info(f"Found options - Call: {call_symbol}, Put: {put_symbol}")
            return call_symbol, put_symbol
            
        except Exception as e:
            logger.error(f"Error finding option symbols: {e}")
            return None, None
    
    def get_option_historical_data(self, symbol: str, resolution: str = '15m', 
                                  hours_back: int = 24) -> List[Dict]:
        """
        Get historical OHLC data for options
        """
        try:
            end_time = int(time.time() * 1000)
            start_time = end_time - (hours_back * 60 * 60 * 1000)
            
            return self.get_historical_ohlc(symbol, resolution, start_time, end_time)
            
        except Exception as e:
            logger.error(f"Error getting historical data for {symbol}: {e}")
            return []
    
    def create_straddle_data(self, call_symbol: str, put_symbol: str, 
                           resolution: str = '15m', hours_back: int = 24) -> List[Dict]:
        """
        Create straddle data by combining call and put option prices
        """
        try:
            call_data = self.get_option_historical_data(call_symbol, resolution, hours_back)
            put_data = self.get_option_historical_data(put_symbol, resolution, hours_back)
            
            if not call_data or not put_data:
                logger.warning("Insufficient data for straddle creation")
                return []
            
            # Create lookup for put data by timestamp
            put_lookup = {candle['start']: candle for candle in put_data}
            
            straddle_data = []
            for call_candle in call_data:
                timestamp = call_candle['start']
                if timestamp in put_lookup:
                    put_candle = put_lookup[timestamp]
                    
                    straddle_candle = {
                        'timestamp': timestamp,
                        'start': timestamp,
                        'open': float(call_candle['open']) + float(put_candle['open']),
                        'high': float(call_candle['high']) + float(put_candle['high']),
                        'low': float(call_candle['low']) + float(put_candle['low']),
                        'close': float(call_candle['close']) + float(put_candle['close']),
                        'volume': float(call_candle['volume']) + float(put_candle['volume'])
                    }
                    straddle_data.append(straddle_candle)
            
            logger.info(f"Created straddle data with {len(straddle_data)} candles")
            return straddle_data
            
        except Exception as e:
            logger.error(f"Error creating straddle data: {e}")
            return []
