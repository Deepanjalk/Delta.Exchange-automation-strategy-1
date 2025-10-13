import ccxt
import os
import time
import schedule
from delta_strategy import run_strategy, reset_daily_state

def job():
    """
    Job to be run by the scheduler.
    """
    # --- DO NOT EDIT ---
    # Use a sandbox account on the testnet for development
    exchange_id = 'delta'
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'apiKey': os.environ.get('DELTA_API_KEY'),
        'secret': os.environ.get('DELTA_API_SECRET'),
        'urls': {
            'api': 'https://testnet-api.delta.exchange',
        },
        'options': {
            'recvWindow': 10000,
        },
    })
    exchange.set_sandbox_mode(True)
    # --- DO NOT EDIT ---

    # Symbol for the underlying asset
    symbol = 'BTC/USDT'

    # Run the trading strategy
    run_strategy(exchange, symbol)

def main():
    """
    Main function to run the trading bot.
    """
    # Run the job immediately for testing
    job()

    # Schedule the job to run every day at 5:35 PM
    schedule.every().day.at("17:35").do(job)
    # Schedule the reset function to run daily at 5:30 PM
    schedule.every().day.at("17:30").do(reset_daily_state)

    print("Scheduler started. Waiting for scheduled jobs...")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    main()
