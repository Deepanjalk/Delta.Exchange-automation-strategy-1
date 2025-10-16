
import os
import time
import schedule
import pytz
from datetime import datetime
from dotenv import load_dotenv
from delta_strategy import TradingStrategy
from delta_api_client import DeltaExchangeAPI
from backtest import run_backtest
import logging

# --- Logger Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("trading_bot.log"),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# --- Exchange Setup ---
def get_api_client():
    """
    Create and return Delta Exchange API client
    """
    api_key = os.environ.get('DELTA_API_KEY')
    api_secret = os.environ.get('DELTA_API_SECRET')
    
    if not api_key or not api_secret:
        raise ValueError("DELTA_API_KEY and DELTA_API_SECRET environment variables must be set")
    
    return DeltaExchangeAPI(api_key, api_secret)

def get_trading_config(api_client=None):
    """
    Get trading configuration from user input or use defaults
    """
    print("\n" + "="*60)
    print("🔧 TRADING CONFIGURATION")
    print("="*60)
    
    # Get lot size
    while True:
        try:
            lot_size_input = input(f"Enter lot size per leg (default: 1): ").strip()
            if lot_size_input == "":
                lot_size = 1
            else:
                lot_size = int(lot_size_input)
                if lot_size <= 0:
                    print("❌ Lot size must be positive. Please try again.")
                    continue
            break
        except ValueError:
            print("❌ Invalid input. Please enter a valid number.")
    
    # Get leverage - fetch available options from Delta Exchange
    leverage = 1  # Default
    if api_client:
        try:
            print("\n🔍 Fetching available leverage options from Delta Exchange...")
            
            # Get BTCUSD product to check leverage
            products = api_client.get_products()
            btc_product = None
            for product in products:
                if product.get('symbol') == 'BTCUSD':
                    btc_product = product
                    break
            
            if btc_product:
                product_id = btc_product['id']
                available_leverage = api_client.get_available_leverage(str(product_id))
                
                if available_leverage:
                    print(f"✅ Available leverage for BTCUSD: {available_leverage}x")
                    leverage_choice = input(f"Use available leverage ({available_leverage}x)? (y/n, default: n): ").strip().lower()
                    if leverage_choice in ['y', 'yes']:
                        leverage = available_leverage
                    else:
                        print(f"⚠️  Note: Delta Exchange offers {available_leverage}x leverage for BTCUSD")
                        leverage_input = input(f"Enter leverage (1-{available_leverage}, default: 1): ").strip()
                        if leverage_input != "":
                            leverage = float(leverage_input)
                            if leverage > available_leverage:
                                print(f"⚠️  Warning: Leverage {leverage}x exceeds Delta Exchange limit of {available_leverage}x")
                else:
                    print("⚠️  Could not fetch leverage info from Delta Exchange")
            else:
                print("⚠️  Could not find BTCUSD product")
                
        except Exception as e:
            print(f"⚠️  Error fetching leverage info: {e}")
            print("Using default leverage of 1x")
    
    # Get max position size
    while True:
        try:
            max_pos_input = input(f"Enter max trades per day (default: 3): ").strip()
            if max_pos_input == "":
                max_position_size = 3
            else:
                max_position_size = int(max_pos_input)
                if max_position_size <= 0:
                    print("❌ Max position size must be positive. Please try again.")
                    continue
            break
        except ValueError:
            print("❌ Invalid input. Please enter a valid number.")
    
    print(f"\n✅ Configuration Set:")
    print(f"   📊 Lot Size: {lot_size} contracts per leg")
    print(f"   ⚡ Leverage: {leverage}x")
    print(f"   🛡️  Max Trades/Day: {max_position_size}")
    print("="*60)
    
    return {
        'lot_size': lot_size,
        'leverage': leverage,
        'max_position_size': max_position_size
    }

def update_config(trading_config):
    """
    Update config.py with new trading parameters
    """
    config_content = f"""# -- Timeframe --
TIMEFRAME = '15m'

# -- Supertrend Settings --
SUPERTREND_LENGTH = 10
SUPERTREND_MULTIPLIER = 3

# -- Trading Settings --
LOT_SIZE = {trading_config['lot_size']}  # Number of contracts per leg
LEVERAGE = {trading_config['leverage']}  # Leverage multiplier (1 = no leverage)
MAX_POSITION_SIZE = {trading_config['max_position_size']}  # Maximum total position size
"""
    
    with open('config.py', 'w') as f:
        f.write(config_content)
    
    logger.info(f"Updated config.py with: LOT_SIZE={trading_config['lot_size']}, LEVERAGE={trading_config['leverage']}, MAX_POSITION_SIZE={trading_config['max_position_size']}")

def main():
    """
    Main function to run the trading bot or backtester.
    """
    try:
        api_client = get_api_client()
        strategy = TradingStrategy(api_client)
    except Exception as e:
        logger.error(f"Failed to initialize API client: {e}")
        return

    def strike_job():
        logger.info("Executing daily strike identification job...")
        try:
            api_client = get_api_client()
            strategy.api_client = api_client  # Update API client for new session
            strategy.set_daily_atm_strike('BTCUSD')
        except Exception as e:
            logger.error(f"Error in strike job: {e}")

    def trade_job():
        logger.info("Executing 15-minute trading job...")
        try:
            api_client = get_api_client()
            strategy.api_client = api_client  # Update API client for new session
            strategy.run_scheduled_strategy('BTCUSD')
        except Exception as e:
            logger.error(f"Error in trade job: {e}")

    def reset_job():
        strategy.reset_daily_state()

    def get_local_schedule_time_for_ist(ist_time_str):
        try:
            ist_tz = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist_tz)
            time_parts = list(map(int, ist_time_str.split(':')))
            target_ist_time = now_ist.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)
            local_tz = datetime.now().astimezone().tzinfo
            local_time = target_ist_time.astimezone(local_tz)
            return local_time.strftime('%H:%M')
        except Exception as e:
            logger.error(f"Could not convert IST time to local time: {e}. Defaulting to original time string.")
            return ist_time_str

    while True:
        logger.info("\n--- Main Menu ---")
        logger.info("1. Run Live Trading Bot")
        logger.info("2. Run Backtester")
        logger.info("3. Configure Trading Settings")
        logger.info("4. Exit")
        choice = input("Enter your choice (1-4): ")

        if choice == '1':
            logger.info("Starting live trading bot...")
            
            # Ask if user wants to configure settings
            config_choice = input("Do you want to configure trading settings? (y/n, default: n): ").strip().lower()
            if config_choice in ['y', 'yes']:
                trading_config = get_trading_config(api_client)
                update_config(trading_config)
                # Reload config after update
                import importlib
                import config
                importlib.reload(config)
                from config import LOT_SIZE, LEVERAGE, MAX_POSITION_SIZE

            reset_time_local = get_local_schedule_time_for_ist("17:30")
            strike_time_local = get_local_schedule_time_for_ist("17:35")

            logger.info(f"Scheduling reset job for {reset_time_local} local time (17:30 IST)")
            logger.info(f"Scheduling strike job for {strike_time_local} local time (17:35 IST)")

            schedule.every().day.at(reset_time_local).do(reset_job)
            schedule.every().day.at(strike_time_local).do(strike_job)

            # Align trading job to the 15-minute candles
            schedule.every().hour.at(":00").do(trade_job)
            schedule.every().hour.at(":15").do(trade_job)
            schedule.every().hour.at(":30").do(trade_job)
            schedule.every().hour.at(":45").do(trade_job)

            logger.info("Scheduler started. Waiting for scheduled jobs...")
            while True:
                schedule.run_pending()
                time.sleep(1)
        elif choice == '2':
            logger.info("Starting backtester...")
            
            # Ask if user wants to configure settings for backtest
            config_choice = input("Do you want to configure trading settings for backtest? (y/n, default: n): ").strip().lower()
            if config_choice in ['y', 'yes']:
                trading_config = get_trading_config(api_client)
                update_config(trading_config)
            
            run_backtest()
            
        elif choice == '3':
            logger.info("Opening trading configuration...")
            trading_config = get_trading_config(api_client)
            update_config(trading_config)
            logger.info("Configuration updated successfully!")
            
        elif choice == '4':
            logger.info("Exiting.")
            break
        else:
            logger.warning("Invalid choice. Please try again.")

if __name__ == '__main__':
    # Entry point for the application
    main()
