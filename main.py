
import ccxt
import os
import time
import schedule
from delta_strategy import set_daily_atm_strike, run_scheduled_strategy, reset_daily_state
from backtest import run_backtest

# --- Exchange Setup ---
def get_exchange():
    exchange_id = 'delta'
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        'apiKey': os.environ.get('DELTA_API_KEY'),
        'secret': os.environ.get('DELTA_API_SECRET'),
        'urls': { 'api': 'https://testnet-api.delta.exchange' },
        'options': { 'recvWindow': 10000 },
    })

def strike_job():
    """Job for setting the daily ATM strike."""
    print("Executing daily strike identification job...")
    exchange = get_exchange()
    exchange.set_sandbox_mode(True)
    set_daily_atm_strike(exchange, 'BTC/USDT')

def trade_job():
    """Job for running the trading strategy."""
    print("Executing 15-minute trading job...")
    exchange = get_exchange()
    exchange.set_sandbox_mode(True)
    run_scheduled_strategy(exchange, 'BTC/USDT')

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
            # Schedule the jobs
            schedule.every().day.at("17:35").do(strike_job)
            schedule.every(15).minutes.do(trade_job)
            schedule.every().day.at("17:30").do(reset_daily_state)

            print("Scheduler started. Running initial strike job...")
            strike_job() # Run once immediately to set the first strike

            print("Waiting for scheduled jobs...")
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
