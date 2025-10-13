
import ccxt
import os
import time
import schedule
from delta_strategy import run_strategy, reset_daily_state
from backtest import run_backtest

def job():
    """
    Job to be run by the scheduler for live trading.
    """
    # --- DO NOT EDIT ---
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

    symbol = 'BTC/USDT'
    run_strategy(exchange, symbol)

def main():
    """
    Main function to run the trading bot or backtester.
    """
    while True:
        print("\n--- Main Menu ---")
        print("1. Run Live Trading Bot")
        print("2. Run Backtester")
        print("3. Exit")
        choice = input("Enter your choice (1-3): ")

        if choice == '1':
            print("Starting live trading bot...")
            job()
            schedule.every().day.at("17:35").do(job)
            schedule.every().day.at("17:30").do(reset_daily_state)
            print("Scheduler started. Waiting for scheduled jobs...")
            while True:
                schedule.run_pending()
                time.sleep(1)
        elif choice == '2':
            run_backtest()
        elif choice == '3':
            print("Exiting.")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == '__main__':
    main()
