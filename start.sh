#!/usr/bin/env bash

# Exit on error
set -e

# Create virtual environment if it doesn't exist
[ ! -d "venv" ] && python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install or upgrade dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Run the bot
echo "Starting Telegram bot..."
python telegram_bot.py