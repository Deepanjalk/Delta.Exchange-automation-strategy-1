# Delta Exchange Trading Bot

This is a Python trading bot that implements the ATM Straddle with Supertrend (10,3) Switching Logic on Delta Exchange.

## Prerequisites

- Python 3.9+
- A Delta Exchange account

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/delta-trading-bot.git
   cd delta-trading-bot
   ```

2. **Install the dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up your API keys:**
   - Create an API key and secret on your Delta Exchange account.
   - Set the following environment variables:
     ```bash
     export DELTA_API_KEY="<YOUR_API_KEY>"
     export DELTA_API_SECRET="<YOUR_SECRET_KEY>"
     ```

## Usage

To run the trading bot, simply execute the `main.py` file:

```bash
python3 main.py
```

The bot will run continuously, checking for trading signals every 15 minutes.
