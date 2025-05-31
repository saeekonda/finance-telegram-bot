import os
import yfinance as yf
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
  # 🔒 Replace with your actual token

# Function to get live stock or crypto price
def get_price(symbol):
    try:
        stock = yf.Ticker(symbol)
        return stock.info["regularMarketPrice"]
    except:
        return None

# Command handler to start the bot
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to your Finance Bot!\n\n"
        "Send me a stock/crypto symbol followed by your fair value.\n"
        "Example: `AAPL 160` or `BTC-USD 70000`",
        parse_mode='Markdown'
    )

# Message handler for symbol and fair value
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper().split()

    if len(text) != 2:
        await update.message.reply_text("❗Please send symbol and fair value like this:\n`TSLA 250`", parse_mode='Markdown')
        return

    symbol, fair_value = text[0], text[1]

    try:
        fair_value = float(fair_value)
        price = get_price(symbol)

        if price is None:
            await update.message.reply_text(f"⚠️ Couldn't fetch price for {symbol}. Try another symbol.")
            return

        response = f"📊 *{symbol}* is trading at *${price}*\n🎯 Your Fair Value: *${fair_value}*\n"

        if price < fair_value:
            response += "💡 *Suggestion*: UNDERVALUED — Consider Buying."
        elif price == fair_value:
            response += "📘 *Suggestion*: Fairly Priced — Hold."
        else:
            response += "⚠️ *Suggestion*: OVERVALUED — Be Cautious."

        await update.message.reply_text(response, parse_mode='Markdown')

    except ValueError:
        await update.message.reply_text("❌ Fair value must be a number. Example: `ETH-USD 3500`", parse_mode='Markdown')

# Main function to run the bot
def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    run_bot()
