# Deployment Instructions for the Delta Exchange Trading Bot

This guide will walk you through the steps to deploy and run the trading bot on your Indian Delta Exchange account.

## Prerequisites

1.  **Python 3.9+**: Ensure you have Python 3.9 or a newer version installed on your system.
2.  **Indian Delta Exchange Account**: You must have an active account on the [Indian Delta Exchange](https://india.delta.exchange/).

## 1. Setting Up the Bot

### Step 1: Clone the Repository

First, clone the repository to your local machine or server:

```bash
git clone <repository_url>
cd <repository_name>
```

### Step 2: Install Dependencies

Install the required Python libraries using the `requirements.txt` file:

```bash
pip install -r requirements.txt
```

### Step 3: Generate API Keys

1.  Log in to your Indian Delta Exchange account.
2.  Navigate to the API section to generate a new API key and secret.
3.  **Important**: Ensure that the API key has the necessary permissions for trading.

### Step 4: Set Environment Variables

The bot reads your API credentials from environment variables. Set them as follows:

**On Linux/macOS:**

```bash
export DELTA_API_KEY="<YOUR_API_KEY>"
export DELTA_API_SECRET="<YOUR_SECRET_KEY>"
```

**On Windows:**

```powershell
$env:DELTA_API_KEY="<YOUR_API_KEY>"
$env:DELTA_API_SECRET="<YOUR_SECRET_KEY>"
```

**Note**: Remember to replace `<YOUR_API_KEY>` and `<YOUR_SECRET_KEY>` with your actual credentials.

## 2. Running the Bot

Once the setup is complete, you can run the bot.

### Step 1: Start the Main Application

Execute the `main.py` script:

```bash
python3 main.py
```

### Step 2: Choose the Live Trading Option

You will be presented with a menu. Choose option `1` to start the live trading bot:

```
--- Main Menu ---
1. Run Live Trading Bot
2. Run Backtester
3. Exit
Enter your choice (1-3): 1
```

The bot will then start, and you will see log messages in your terminal.

## 3. Keeping the Bot Running (Important)

For the bot to work correctly, it needs to run continuously. If you close your terminal, the bot will stop. To keep it running on a server, you can use one of the following methods:

### Using `nohup`

The `nohup` command allows you to run a command in the background, and it will continue to run even after you log out.

```bash
nohup python3 main.py &
```

The output of the bot will be saved to a file named `nohup.out`. You can view the logs using:

```bash
tail -f nohup.out
```

### Using `screen`

`screen` is a powerful tool that allows you to manage multiple terminal sessions.

1.  **Start a new screen session:**
    ```bash
    screen -S trading_bot
    ```

2.  **Run the bot inside the screen session:**
    ```bash
    python3 main.py
    ```

3.  **Detach from the screen session** by pressing `Ctrl+A` followed by `d`. The bot will continue to run.

4.  **To reattach to the session later:**
    ```bash
    screen -r trading_bot
    ```

## Security Best Practices

-   **Never** share your API keys with anyone.
-   **Never** commit your API keys to a Git repository.
-   Consider using IP whitelisting for your API keys if your server has a static IP address.
