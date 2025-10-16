# Delta Exchange Trading Bot

This is a Python trading bot that implements the ATM Straddle with Supertrend (10,3) Switching Logic on Delta Exchange using the native Delta Exchange REST API.

## Features

- **Native Delta Exchange API Integration**: Direct implementation using Delta Exchange's REST API with HMAC authentication
- **ATM Straddle Strategy**: Automatically identifies at-the-money strike prices and creates straddle positions
- **Supertrend Indicator**: Uses Supertrend (10,3) for trend following signals
- **Automated Trading**: Runs on scheduled intervals with position management
- **Backtesting**: Historical simulation capabilities
- **Risk Management**: Built-in position limits and safety mechanisms

## Prerequisites

- Python 3.9+
- A Delta Exchange account with API access
- API key and secret from Delta Exchange

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
   - Create an API key and secret on your Delta Exchange account
   - Create a `.env` file in the project root with:
     ```
     DELTA_API_KEY=your_api_key_here
     DELTA_API_SECRET=your_secret_key_here
     ```
   - Or set environment variables:
     ```bash
     export DELTA_API_KEY="<YOUR_API_KEY>"
     export DELTA_API_SECRET="<YOUR_SECRET_KEY>"
     ```

## Usage

To run the trading bot, execute the `main.py` file:

```bash
python3 main.py
```

The bot provides three options:
1. **Live Trading Bot**: Runs the automated strategy with real trading
2. **Backtester**: Simulates the strategy on historical data
3. **Exit**: Quit the application

### Trading Schedule

- **Daily Reset**: 5:30 PM IST
- **Strike Identification**: 5:35 PM IST
- **Trading Signals**: Every 15 minutes (00, 15, 30, 45 past each hour)

## Architecture

### Core Components

- **`delta_api_client.py`**: Native Delta Exchange API client with HMAC authentication
- **`delta_strategy.py`**: Trading strategy implementation with straddle logic
- **`main.py`**: Main application with scheduling and menu system
- **`backtest.py`**: Historical backtesting simulation
- **`config.py`**: Configuration parameters (timeframe, Supertrend settings)

### API Implementation

The bot uses Delta Exchange's native REST API with:
- HMAC-SHA256 authentication
- All major endpoints (products, orders, positions, historical data)
- Option chain data fetching
- Real-time market data

## Strategy Logic

1. **ATM Strike Identification**: Finds the closest strike price to current underlying price
2. **Option Symbol Discovery**: Locates call and put options for the ATM strike
3. **Straddle Construction**: Combines call and put prices to create straddle data
4. **Supertrend Analysis**: Calculates trend direction using Supertrend indicator
5. **Position Management**: 
   - Creates straddle when trend turns bearish
   - Closes losing leg when trend turns bullish
   - Reconstructs straddle when partially closed

## Risk Management

- Maximum 3 trades per day
- Automatic position closure on critical errors
- Order retry mechanism with timeout
- Comprehensive logging and error handling

## Disclaimer

This software is for educational and research purposes only. Trading cryptocurrencies involves substantial risk of loss. Use at your own risk.
