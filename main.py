
import ccxt
import os
import time
import schedule
import pytz
from datetime import datetime
from dotenv import load_dotenv
from delta_strategy import TradingStrategy
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
def get_exchange():
    exchange_id = 'delta'
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        'apiKey': os.environ.get('DELTA_API_KEY'),
        'secret': os.environ.get('DELTA_API_SECRET'),
        'urls': {
            'api': {
                'public': 'https://api.india.delta.exchange',
                'private': 'https://api.india.delta.exchange',
            },
        },
        'options': { 'recvWindow': 10000 },
    })

def main():
    """
    Main function to run the trading bot or backtester.
    """
    strategy = TradingStrategy()

    def strike_job():
        logger.info("Executing daily strike identification job...")
        exchange = get_exchange()
        strategy.set_daily_atm_strike(exchange, 'BTC/USD')

    def trade_job():
        logger.info("Executing 15-minute trading job...")
        exchange = get_exchange()
        strategy.run_scheduled_strategy(exchange)

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
        logger.info("3. Exit")
        choice = input("Enter your choice (1-3): ")

        if choice == '1':
            logger.info("Starting live trading bot...")

            reset_time_local = get_local_schedule_time_for_ist("17:30")
            strike_time_local = get_local_schedule_time_for_ist("17:35")

            logger.info(f"Scheduling reset job for {reset_time_local} local time (17:30 IST)")
            logger.info(f"Scheduling strike job for {strike_time_local} local time (17:35 IST)")

            schedule.every().day.at(reset_time_local).do(reset_job)
            schedule.every().day.at(strike_time_local).do(strike_job)
            schedule.every(15).minutes.do(trade_job)

            logger.info("Scheduler started. Running initial strike job...")
            strike_job()

            logger.info("Waiting for scheduled jobs...")
            while True:
                schedule.run_pending()
                time.sleep(1)
        elif choice == '2':
            run_backtest()
        elif choice == '3':
            logger.info("Exiting.")
            break
        else:
            logger.warning("Invalid choice. Please try again.")

if __name__ == '__main__':
    # Entry point for the application
    main()
