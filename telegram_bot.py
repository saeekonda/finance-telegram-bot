import os
import logging
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    Application,
    JobQueue,
)
import openai
import requests
from threading import Thread
import time
from datetime import datetime, timedelta
import asyncio

# Import the keep_alive function
try:
    from keep_alive import keep_alive
except ImportError:
    logging.error(
        "Could not import keep_alive.py. Ensure it's in the same directory.")

    def keep_alive():
        print("keep_alive function not found. Skipping web server startup.")


# --- Load .env file ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# --- Set API key ---
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    logging.warning("OPENAI_API_KEY is not set. AI features will not work.")

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Global storage for tracked stocks (for simplicity; for production, use a database) ---
# Format: { 'chat_id': { 'symbol': { 'target_price': float, 'direction': 'above'/'below' } } }
TRACKED_STOCKS_FILE = 'tracked_stocks.json'


def load_tracked_stocks():
    """Loads tracked stocks from a JSON file."""
    if os.path.exists(TRACKED_STOCKS_FILE):
        try:
            with open(TRACKED_STOCKS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(
                f"Error decoding {TRACKED_STOCKS_FILE}: {e}. Starting with empty tracked stocks."
            )
            return {}
    return {}


def save_tracked_stocks(data):
    """Saves tracked stocks to a JSON file."""
    try:
        with open(TRACKED_STOCKS_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except IOError as e:
        logger.error(f"Error saving {TRACKED_STOCKS_FILE}: {e}")


tracked_stocks = load_tracked_stocks()
logger.info(f"Loaded tracked stocks: {tracked_stocks}")


# --- Helper function for AI summarization/recommendation ---
async def generate_ai_response(prompt_text, max_tokens=500):
    """Generates a response using OpenAI's GPT model."""
    if not OPENAI_API_KEY:
        return "🤖 My AI brain is offline! The OpenAI API key is missing. Please contact the bot's administrator."
    try:
        response = openai.ChatCompletion.create(model="gpt-3.5-turbo",
                                                messages=[{
                                                    "role":
                                                    "user",
                                                    "content":
                                                    prompt_text
                                                }],
                                                temperature=0.7,
                                                max_tokens=max_tokens)
        return response["choices"][0]["message"]["content"]
    except openai.error.AuthenticationError:
        logger.error("OpenAI API authentication failed. Check your API key.")
        return "🤖 I'm having trouble connecting to my AI brain. It seems my OpenAI API key might be invalid. Please alert the bot's administrator!"
    except openai.error.RateLimitError:
        logger.warning("OpenAI API rate limit exceeded.")
        return "🤖 Woah, slow down! I'm getting too many requests right now. Please try again in a moment."
    except openai.error.OpenAIError as e:
        logger.error(f"OpenAI API error: {e}")
        return f"🤖 I'm having trouble connecting to my AI brain right now. Please try again later. (Error: {e})"
    except Exception as e:
        logger.error(f"Error generating AI response: {e}")
        return "Oops! An unexpected error occurred while processing your AI request. My apologies!"


# --- Telegram Bot Commands (defined before main) ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hello there! I'm your friendly *FinBot* 🤖 – your personal finance companion.\n\n"
        "I'm here to make understanding the stock market easier and help you stay on top of your investments. "
        "Here's what I can do for you:\n\n"
        "📈 `/stock <symbol>` — Get live price, key details & *an easy-to-read summary* (e.g., `/stock AAPL`)\n"
        "📊 `/analyze <symbol>` — Dive deeper! Get P/E, market cap, and *AI-powered financial insights* (e.g., `/analyze MSFT`)\n"
        "🗞️ `/news` — Catch up on the *latest global finance headlines*.\n"
        "🔍 `/stocknews <symbol>` — Get *company-specific news* (e.g., `/stocknews GOOG`)\n"
        "❓ `/ask <question>` — Ask *any finance/investment question* and I'll explain it simply (e.g., `/ask What is inflation?`)\n"
        "💡 `/recommend <symbol>` — Get an *AI-driven Buy/Sell/Hold outlook* based on recent news (Experimental)\n\n"
        "🔔 *Price Alerts!* Never miss a beat:\n"
        "👉 `/track <symbol> <price> <above/below>` — Set a price alert (e.g., `/track GOOG 180 above` or `/track AMZN 170 below`)\n"
        "👉 `/myalerts` — See all your active price alerts.\n"
        "👉 `/untrack <symbol>` — Stop tracking a stock.\n\n"
        "✨ My goal is to simplify complex financial info just for YOU! Feel free to ask anything.\n\n"
        "❗ *Important Disclaimer:* All information provided is for educational and informational purposes only and *does NOT constitute financial advice*. Always do your own research or consult a professional financial advisor before making any investment decisions. I'm just a bot here to help you understand better! 😊",
        parse_mode="Markdown")


async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = " ".join(context.args).upper()
    if not symbol:
        await update.message.reply_text(
            "❗Oops! Please tell me which stock you're interested in. Try: `/stock IBM`",
            parse_mode="Markdown")
        return

    if not ALPHA_VANTAGE_API_KEY:
        await update.message.reply_text(
            "❗My apologies! Alpha Vantage API key is missing. I can't fetch stock data without it. Please ensure `ALPHA_VANTAGE_API_KEY` is set correctly in your `.env` file. 🙏"
        )
        return

    await update.message.reply_text(f"🔍 Fetching live data for *{symbol}*...",
                                    parse_mode="Markdown")

    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status(
        )  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()

        global_quote = data.get("Global Quote", {})

        if global_quote and global_quote.get("01. symbol"):
            name = global_quote.get("01. symbol", symbol)
            price = float(global_quote.get("05. price", 0))
            change = float(global_quote.get("09. change", 0))
            change_percent = float(
                global_quote.get("10. change percent", "0%").replace('%', ''))
            volume = int(global_quote.get("06. volume", 0))

            currency = "USD"

            change_emoji = '🟢' if change > 0 else ('🔴' if change < 0 else '⚪')

            summary = (
                f"📈 *{name}* — Current Price\n\n"
                f"Current Price: {price:.2f} {currency}\n"
                f"Today's Change: {change:+.2f} ({change_percent:+.2f}%) {change_emoji}\n"
                f"Volume: {volume:,}\n\n"
                f"Want more details? Try `/analyze {symbol}` for deep insights! ✨"
            )
            await update.message.reply_text(summary, parse_mode="Markdown")

        else:
            await update.message.reply_text(
                "⚠️ Oh dear! I couldn't find real-time stock data for that symbol using Alpha Vantage. Please double-check the symbol (e.g., `AAPL`, `GOOG`). Keep in mind Alpha Vantage has API limits. 🕰️"
            )

    except requests.exceptions.HTTPError as e:
        logger.error(
            f"HTTP Error fetching Alpha Vantage data for {symbol}: {e} - Response: {response.text}"
        )
        await update.message.reply_text(
            f"⚠️ Alpha Vantage API Error for {symbol}: An HTTP error occurred ({e.response.status_code}). This often happens if the symbol is incorrect, or if you've hit your API call limits (Alpha Vantage has limits for free tier). Please try again after a minute or check the symbol. 🕰️"
        )
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching Alpha Vantage data for {symbol}.")
        await update.message.reply_text(
            "⚠️ Request timed out while fetching stock data. The Alpha Vantage API might be slow. Please try again. 🐢"
        )
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Network/Request error fetching Alpha Vantage data for {symbol}: {e}",
            exc_info=True)
        await update.message.reply_text(
            "⚠️ A network error occurred while fetching stock data. Please check your internet connection or try again later."
        )
    except json.JSONDecodeError as e:
        logger.error(
            f"JSON Decode Error for Alpha Vantage response for {symbol}: {e} - Raw response: {response.text}"
        )
        await update.message.reply_text(
            "⚠️ I received a malformed response from Alpha Vantage. Please try again later. 🛠️"
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching stock data for {symbol}: {e}",
                     exc_info=True)
        await update.message.reply_text(
            "⚠️ Oops! An unexpected error occurred while fetching stock data. My apologies! 😔"
        )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = " ".join(context.args).upper()
    if not symbol:
        await update.message.reply_text(
            "❗To get a detailed financial analysis, please tell me the stock symbol. Example: `/analyze IBM`",
            parse_mode="Markdown")
        return

    if not ALPHA_VANTAGE_API_KEY:
        await update.message.reply_text(
            "❗My apologies! Alpha Vantage API key is missing. I can't fetch detailed financial data without it. Please ensure `ALPHA_VANTAGE_API_KEY` is set correctly in your `.env` file. 🙏"
        )
        return

    await update.message.reply_text(
        f"🚀 Digging deep into *{symbol}*'s financials... This might take a moment as I gather and process the data. Hang tight! 🧐",
        parse_mode="Markdown")

    try:
        # Fetch Company Overview for P/E and other ratios
        overview_url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
        overview_response = requests.get(overview_url, timeout=10).json()

        # Fetch Income Statement (latest annual)
        income_stmt_url = f"https://www.alphavantage.co/query?function=INCOME_STATEMENT&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
        income_response = requests.get(income_stmt_url, timeout=10).json()

        # Fetch Balance Sheet (latest annual)
        balance_sheet_url = f"https://www.alphavantage.co/query?function=BALANCE_SHEET&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
        balance_response = requests.get(balance_sheet_url, timeout=10).json()

        # Fetch Cash Flow (latest annual)
        cash_flow_url = f"https://www.alphavantage.co/query?function=CASH_FLOW&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
        cash_flow_response = requests.get(cash_flow_url, timeout=10).json()

        # Check for API call limits or errors from Alpha Vantage
        api_error = False
        error_messages = []
        # Alpha Vantage returns "Error Message" or "Information" for limits/invalid symbols
        responses_to_check = {
            "Overview": overview_response,
            "Income Statement": income_response,
            "Balance Sheet": balance_response,
            "Cash Flow": cash_flow_response
        }

        for key, resp in responses_to_check.items():
            if resp.get("Error Message") or resp.get("Information"):
                msg = resp.get(
                    "Error Message",
                    resp.get("Information", f"Unknown API error in {key}."))
                error_messages.append(f"{key}: {msg}")
                api_error = True

        if api_error:
            full_error_msg = "\n".join(error_messages)
            await update.message.reply_text(
                f"⚠️ Alpha Vantage API Error for {symbol}:\n{full_error_msg}\n\nThis often happens if the symbol is incorrect, or if you've hit your API call limits (Alpha Vantage has limits for free tier). Please try again after a minute or check the symbol. 🕰️"
            )
            return

        financial_data_for_ai = {}
        report_summary = ""

        # Process Overview Data
        if overview_response and not overview_response.get("Error Message"):
            financial_data_for_ai['Overview'] = overview_response
            report_summary += f"Company Overview:\n"
            # Use .get() with default values and robust conversion
            description = overview_response.get('Description', 'N/A')
            report_summary += f"  Description: {description[:200]}{'...' if len(description) > 200 else ''}\n"
            report_summary += f"  Exchange: {overview_response.get('Exchange', 'N/A')}\n"
            report_summary += f"  Currency: {overview_response.get('Currency', 'N/A')}\n"
            report_summary += f"  Sector: {overview_response.get('Sector', 'N/A')}\n"
            report_summary += f"  Industry: {overview_response.get('Industry', 'N/A')}\n"
            # Safely convert and format large numbers
            market_cap = float(overview_response.get('MarketCapitalization',
                                                     0))
            report_summary += f"  Market Cap: ${int(market_cap):,}\n"
            report_summary += f"  P/E Ratio: {overview_response.get('PERatio', 'N/A')}\n"
            report_summary += f"  EPS: {overview_response.get('EPS', 'N/A')}\n"
            report_summary += f"  Dividend Yield: {float(overview_response.get('DividendYield', 0)):.2f}%\n"  # Ensure float for formatting
            report_summary += f"  52 Week High: {overview_response.get('52WeekHigh', 'N/A')}\n"
            report_summary += f"  52 Week Low: {overview_response.get('52WeekLow', 'N/A')}\n\n"
        else:
            report_summary += "Company Overview data not available.\n\n"

        # Process Income Statement (latest annual)
        if "annualReports" in income_response and income_response[
                "annualReports"]:
            latest_income = income_response["annualReports"][0]
            financial_data_for_ai['Income Statement'] = latest_income
            report_summary += "*Income Statement (Latest Annual):*\n"
            report_summary += f"  Fiscal Date Ending: {latest_income.get('fiscalDateEnding', 'N/A')}\n"
            report_summary += f"  Total Revenue: ${int(float(latest_income.get('totalRevenue', 0))):,}\n"
            report_summary += f"  Gross Profit: ${int(float(latest_income.get('grossProfit', 0))):,}\n"
            report_summary += f"  Operating Income: ${int(float(latest_income.get('operatingIncome', 0))):,}\n"
            report_summary += f"  Net Income: ${int(float(latest_income.get('netIncome', 0))):,}\n"
            report_summary += f"  EBITDA: ${int(float(latest_income.get('ebitda', 0))):,}\n\n"
        else:
            report_summary += "Income Statement data not available.\n\n"

        # Process Balance Sheet (latest annual)
        if "annualReports" in balance_response and balance_response[
                "annualReports"]:
            latest_balance = balance_response["annualReports"][0]
            financial_data_for_ai['Balance Sheet'] = latest_balance
            report_summary += "*Balance Sheet (Latest Annual):*\n"
            report_summary += f"  Fiscal Date Ending: {latest_balance.get('fiscalDateEnding', 'N/A')}\n"
            report_summary += f"  Total Assets: ${int(float(latest_balance.get('totalAssets', 0))):,}\n"
            report_summary += f"  Total Liabilities: ${int(float(latest_balance.get('totalLiabilities', 0))):,}\n"
            report_summary += f"  Total Shareholder Equity: ${int(float(latest_balance.get('totalShareholderEquity', 0))):,}\n"
            report_summary += f"  Cash & Equivalents: ${int(float(latest_balance.get('cashAndCashEquivalentsAtCarryingValue', 0))):,}\n\n"
        else:
            report_summary += "Balance Sheet data not available.\n\n"

        # Process Cash Flow (latest annual)
        if "annualReports" in cash_flow_response and cash_flow_response[
                "annualReports"]:
            latest_cash_flow = cash_flow_response["annualReports"][0]
            financial_data_for_ai['Cash Flow Statement'] = latest_cash_flow
            report_summary += "*Cash Flow Statement (Latest Annual):*\n"
            report_summary += f"  Fiscal Date Ending: {latest_cash_flow.get('fiscalDateEnding', 'N/A')}\n"
            report_summary += f"  Operating Cash Flow: ${int(float(latest_cash_flow.get('operatingCashflow', 0))):,}\n"
            report_summary += f"  Investing Cash Flow: ${int(float(latest_cash_flow.get('cashflowFromInvesting', 0))):,}\n"
            report_summary += f"  Financing Cash Flow: ${int(float(latest_cash_flow.get('cashflowFromFinancing', 0))):,}\n\n"
        else:
            report_summary += "Cash Flow Statement data not available.\n\n"

        # Use AI to summarize and interpret
        summary_prompt = (
            f"As a friendly and insightful financial AI, summarize the key financial highlights and health of {symbol} "
            f"based on the following raw data. Explain the significance of metrics like P/E ratio, revenue, net income, and cash flow "
            f"in simple terms. Focus on what these numbers *mean* for an average investor. Keep it concise, engaging, and easy to understand.\n\n"
            f"Raw Financial Data for {symbol}:\n{report_summary}")
        ai_summary_and_explanation = await generate_ai_response(
            summary_prompt,
            max_tokens=700)  # Allow more tokens for detailed summary

        await update.message.reply_text(
            f"✨ *Financial Deep Dive: {symbol}* ✨\n\n"
            f"{ai_summary_and_explanation}\n\n"
            f"---📊 Raw Data Snippets for Your Reference 📊---\n"
            f"{report_summary}\n"
            f"❗ *Disclaimer:* This AI summary is for informational purposes only and is not financial advice. Do your own research! 🧐",
            parse_mode="Markdown")

    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching financial data for {symbol}.")
        await update.message.reply_text(
            "⚠️ Request timed out. The financial data API might be slow. Please try again. 🐢"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Network/Request error for /analyze {symbol}: {e}",
                     exc_info=True)
        await update.message.reply_text(
            "⚠️ A network error occurred while fetching detailed financial data. Please check your internet connection or try again later."
        )
    except Exception as e:
        logger.error(f"/analyze error for {symbol}: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Oh no! I hit a snag while trying to fetch the detailed financial data. It could be an invalid symbol, or an API issue. Please double-check the symbol and try again. 👷‍♂️"
        )


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not NEWS_API_KEY:
        await update.message.reply_text(
            "❗My apologies! News API key is missing. I can't fetch news without it. Please ensure `NEWS_API_KEY` is set correctly in your `.env` file. 🙏"
        )
        return
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={NEWS_API_KEY}"
        response = requests.get(url, timeout=10).json()

        if response.get("status") == "error":
            logger.error(
                f"NewsAPI error: {response.get('message', 'Unknown error')}")
            await update.message.reply_text(
                f"⚠️ News API Error: {response.get('message', 'Unable to fetch news.')} Please check your NewsAPI key or try again later."
            )
            return

        articles = response.get("articles", [])[:5]

        if not articles:
            await update.message.reply_text(
                "📰 No top finance news available at the moment. The news outlets might be taking a break! 😴"
            )
            return

        news_text = "🗞️ *Top Global Finance News Headlines:*\n\n"
        for i, article in enumerate(articles):
            title = article.get('title', 'No Title')
            source = article.get('source', {}).get('name', 'Unknown Source')
            url = article.get('url', '#')
            news_text += f"{i+1}. [{title}]({url})\n    _Source: {source}_\n\n"

        await update.message.reply_text(news_text,
                                        parse_mode="Markdown",
                                        disable_web_page_preview=True)

    except requests.exceptions.Timeout:
        logger.error("Timeout fetching general news.")
        await update.message.reply_text(
            "⚠️ Request timed out while fetching news. The news API might be slow. Please try again. 🐢"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Network/Request error for /news: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ A network error occurred while fetching news. Please check your internet connection or try again later."
        )
    except Exception as e:
        logger.error(f"/news error: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Unable to fetch news. Perhaps the news channels are quiet today! Please try again later. 🤷‍♀️"
        )


async def stock_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = " ".join(context.args).upper()
    if not symbol:
        await update.message.reply_text(
            "❗To get news for a specific stock, please provide its symbol. Example: `/stocknews AAPL`",
            parse_mode="Markdown")
        return

    if not ALPHA_VANTAGE_API_KEY:
        await update.message.reply_text(
            "❗My apologies! Alpha Vantage API key is missing. I can't fetch stock-specific news without it. Please ensure `ALPHA_VANTAGE_API_KEY` is set correctly in your `.env` file. 🙏"
        )
        return

    await update.message.reply_text(
        f"📰 Searching for the latest news on *{symbol}*...",
        parse_mode="Markdown")

    try:
        url = (
            f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}"
            f"&time_from={(datetime.now() - timedelta(days=7)).strftime('%Y%m%dT%H%M')}&sort=LATEST&limit=10&apikey={ALPHA_VANTAGE_API_KEY}"  # Get more articles for better analysis
        )
        response = requests.get(url, timeout=10).json()
        articles = response.get("feed", [])

        # Check for Alpha Vantage API call limits or errors
        if "Error Message" in response or "Information" in response:
            error_msg = response.get(
                "Error Message",
                response.get("Information", "Unknown API error."))
            await update.message.reply_text(
                f"⚠️ Alpha Vantage API Error for {symbol}: {error_msg}. This might be due to an incorrect symbol or API call limits. Please try again after a minute. 🕰️"
            )
            return

        if not articles:
            await update.message.reply_text(
                f"😔 No recent news with sentiment data found for *{symbol}*. It seems to be a quiet day for this company! 🤫",
                parse_mode="Markdown")
            return

        news_text = f"📰 *Recent News for {symbol}:*\n\n"
        for i, article in enumerate(articles):
            title = article.get('title', 'No Title')
            url = article.get('url', '#')
            source = article.get('source', 'Unknown Source')
            news_text += f"{i+1}. [{title}]({url})\n    _Source: {source}_\n\n"

        await update.message.reply_text(news_text,
                                        parse_mode="Markdown",
                                        disable_web_page_preview=True)

    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching stock news for {symbol}.")
        await update.message.reply_text(
            "⚠️ Request timed out while fetching stock news. The Alpha Vantage API might be slow. Please try again. 🐢"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Network/Request error for /stocknews {symbol}: {e}",
                     exc_info=True)
        await update.message.reply_text(
            "⚠️ A network error occurred while fetching stock news. Please check your internet connection or try again later."
        )
    except Exception as e:
        logger.error(f"/stocknews error for {symbol}: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Oops! Couldn't fetch news for that stock. It might be an invalid symbol or a temporary API issue. 🤔"
        )


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text(
            "❓ What's on your mind? Ask me anything about finance or investments! Example: `/ask What is the difference between stocks and bonds?`",
            parse_mode="Markdown")
        return

    await update.message.reply_text(
        "🧠 Thinking... I'm consulting my financial knowledge base to give you the best answer! This might take a moment. ⏳"
    )

    try:
        # Get top news headlines for context
        news_url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={NEWS_API_KEY}"
        news_context = ""
        if NEWS_API_KEY:  # Only try to fetch if API key is present
            try:
                news_response = requests.get(news_url, timeout=5).json()
                if news_response.get("status") == "error":
                    logger.warning(
                        f"NewsAPI error for /ask context: {news_response.get('message', 'Unknown error')}. Proceeding without news context."
                    )
                    news_context = "No recent finance headlines available due to a NewsAPI error. Proceeding with general knowledge.\n"
                else:
                    articles = news_response.get(
                        "articles",
                        [])[:3]  # Get up to 3 top articles for conciseness
                    if articles:
                        headlines = "\n".join(
                            [f"- {article['title']}" for article in articles])
                        news_context = f"--- Latest Finance Headlines for Context ---\n{headlines}\n"
                    else:
                        news_context = "No recent finance headlines available for context.\n"
            except requests.exceptions.Timeout:
                logger.warning(
                    "Timeout fetching news for /ask context. Proceeding without news context."
                )
                news_context = "No recent finance headlines available due to a timeout. Proceeding with general knowledge.\n"
            except requests.exceptions.RequestException as e:
                logger.warning(
                    f"Network/Request error fetching news for /ask context: {e}. Proceeding without news context."
                )
                news_context = "No recent finance headlines available due to a network error. Proceeding with general knowledge.\n"
            except Exception as e:
                logger.warning(
                    f"Error fetching news for /ask context: {e}. Proceeding without news context."
                )
                news_context = "No recent finance headlines available due to an error. Proceeding with general knowledge.\n"
        else:
            news_context = "News API key missing, proceeding with general knowledge.\n"

        prompt = (
            f"You are FinBot 🤖, an advanced, friendly, and helpful finance AI assistant. "
            f"Your goal is to provide information in an easy-to-understand and simple manner. "
            f"Answer the user's question concisely, using the provided context if relevant, but do not directly quote it. "
            f"If the context is not directly relevant or insufficient, use your extensive general financial knowledge. "
            f"Always prioritize providing factual, balanced, and simplified financial information. "
            f"Do not give direct investment advice, only informational insights and explanations. "
            f"Use emojis to make the explanation more engaging and friendly.\n\n"
            f"{news_context}"
            f"-------------------------------------------\n\n"
            f"User Query: {question}")

        answer = await generate_ai_response(
            prompt,
            max_tokens=600)  # Allow more tokens for comprehensive answers
        await update.message.reply_text(answer)

    except Exception as e:
        logger.error(f"/ask error: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ I encountered an issue while trying to answer your question. Please try rephrasing it or try again later. 😓"
        )


async def recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = " ".join(context.args).upper()
    if not symbol:
        await update.message.reply_text(
            "❗To get an AI-driven outlook, please provide a stock symbol. Example: `/recommend TSLA`",
            parse_mode="Markdown")
        return

    if not ALPHA_VANTAGE_API_KEY:
        await update.message.reply_text(
            "❗My apologies! Alpha Vantage API key is missing. I can't provide news-based recommendations without it. Please ensure `ALPHA_VANTAGE_API_KEY` is set correctly in your `.env` file. 🙏"
        )
        return

    await update.message.reply_text(
        f"🤖 Analyzing recent news sentiment for *{symbol}* to give you an an AI-driven outlook... This is an experimental feature, so please take it as a general insight, not financial advice! 🧐",
        parse_mode="Markdown")

    try:
        # Fetch news sentiment from Alpha Vantage
        sentiment_url = (
            f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}"
            f"&time_from={(datetime.now() - timedelta(days=7)).strftime('%Y%m%dT%H%M')}&sort=LATEST&limit=10&apikey={ALPHA_VANTAGE_API_KEY}"  # Get more articles for better analysis
        )
        response = requests.get(sentiment_url, timeout=15).json()
        articles = response.get("feed", [])

        # Check for Alpha Vantage API call limits or errors
        if "Error Message" in response or "Information" in response:
            error_msg = response.get(
                "Error Message",
                response.get("Information", "Unknown API error."))
            await update.message.reply_text(
                f"⚠️ Alpha Vantage API Error for {symbol}: {error_msg}. This might be due to an incorrect symbol or API call limits. Please try again after a minute. 🕰️"
            )
            return

        if not articles:
            await update.message.reply_text(
                f"😔 No recent news with sentiment data found for *{symbol}*. I need news to form an opinion! Can't give a recommendation right now. 🤷‍♀️",
                parse_mode="Markdown")
            return

        # Prepare news for OpenAI
        news_summaries = []
        for i, article in enumerate(articles):
            title = article.get('title', 'No Title')
            summary = article.get('summary', 'No summary available.')
            overall_sentiment_score = article.get('overall_sentiment_score',
                                                  'N/A')
            overall_sentiment_label = article.get('overall_sentiment_label',
                                                  'N/A')

            news_summaries.append(
                f"Article {i+1}:\n"
                f"Title: {title}\n"
                f"Summary: {summary}\n"
                f"Overall Sentiment: {overall_sentiment_label} (Score: {overall_sentiment_score})\n"
            )

        news_context = "\n---\n".join(news_summaries)

        recommendation_prompt = (
            f"As FinBot 🤖, an AI, based on the following recent news articles and their sentiment analysis for the company '{symbol}', "
            f"provide a brief, easy-to-understand summary of the overall news sentiment (positive, negative, mixed) and a general 'Buy', 'Hold', or 'Sell' outlook based *solely on this news sentiment*. "
            f"Explain your reasoning clearly in 2-3 sentences. "
            f"Use emojis to make it friendly. "
            f"Crucially, add a *strong disclaimer* that this is an AI-generated experimental opinion based on news sentiment and *not financial advice*. "
            f"Emphasize the importance of doing personal research and consulting professionals.\n\n"
            f"News Articles for {symbol}:\n{news_context}")

        ai_recommendation = await generate_ai_response(
            recommendation_prompt,
            max_tokens=300)  # Keep recommendations concise
        await update.message.reply_text(ai_recommendation,
                                        parse_mode="Markdown")

    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching news sentiment for {symbol}.")
        await update.message.reply_text(
            "⚠️ Request timed out while fetching news sentiment. The API might be slow. Please try again. 🐢"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Network/Request error for /recommend {symbol}: {e}",
                     exc_info=True)
        await update.message.reply_text(
            "⚠️ A network error occurred while fetching news sentiment for recommendation. Please check your internet connection or try again later."
        )
    except Exception as e:
        logger.error(f"/recommend error for {symbol}: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Oops! Couldn't generate an AI recommendation. It might be an invalid symbol or a temporary API issue. 🤔"
        )


# --- Price Alert Features (using JobQueue) ---


# THIS SECTION HAS BEEN MOVED UP TO BE DEFINED BEFORE main()
async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback function to periodically check all active price alerts."""
    global tracked_stocks
    logger.info("Checking price alerts...")
    # Use a copy to allow modification during iteration
    alerts_to_remove = []

    for chat_id_str, symbols_data in list(tracked_stocks.items()):
        chat_id = int(chat_id_str)  # Convert back to int for send_message
        for symbol, alert_data in list(symbols_data.items()):
            target_price = alert_data['target_price']
            direction = alert_data['direction']

            try:
                # Fetch current price (using Alpha Vantage Global Quote)
                current_price = None
                if ALPHA_VANTAGE_API_KEY:
                    av_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
                    try:
                        response = requests.get(av_url, timeout=5)
                        response.raise_for_status()
                        av_data = response.json()
                        av_global_quote = av_data.get("Global Quote", {})
                        if av_global_quote and av_global_quote.get(
                                "05. price"):
                            current_price = float(av_global_quote["05. price"])
                        else:
                            logger.warning(
                                f"Alpha Vantage did not return price for {symbol} during alert check. Response: {av_data}"
                            )
                            # If AV returns info about API limits, handle it.
                            if "Information" in av_data:
                                logger.warning(
                                    f"Alpha Vantage info for {symbol}: {av_data['Information']}"
                                )
                                # Optionally, notify user about rate limits if it's the first time
                                # await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Alpha Vantage API limits hit for {symbol}. Price alerts might be delayed.")
                                continue  # Skip this symbol for now

                    except (requests.exceptions.RequestException,
                            json.JSONDecodeError, KeyError) as e:
                        logger.warning(
                            f"Failed to get price for {symbol} from Alpha Vantage during alert check: {e}"
                        )
                        continue  # Skip to next alert if AV fails

                if current_price is None:
                    logger.warning(
                        f"Could not get current price for {symbol} for alert check in chat {chat_id} (API key missing or no data)."
                    )
                    continue

                alert_triggered = False
                message = ""

                if direction == 'above' and current_price >= target_price:
                    message = (
                        f"🔔 *Price Alert!* 🔔\n\n"
                        f"*{symbol}* has reached or surpassed your target price!\n"
                        f"Current Price: {current_price:.2f}\n"
                        f"Target Price: {target_price:.2f} (above)")
                    alert_triggered = True
                elif direction == 'below' and current_price <= target_price:
                    message = (
                        f"🔔 *Price Alert!* 🔔\n\n"
                        f"*{symbol}* has fallen to or below your target price!\n"
                        f"Current Price: {current_price:.2f}\n"
                        f"Target Price: {target_price:.2f} (below)")
                    alert_triggered = True

                if alert_triggered:
                    await context.bot.send_message(chat_id=chat_id,
                                                   text=message,
                                                   parse_mode="Markdown")
                    logger.info(
                        f"Alert triggered for {symbol} at {current_price} for chat {chat_id}."
                    )
                    # Mark alert for removal after it's triggered
                    alerts_to_remove.append((chat_id_str, symbol))

            except Exception as e:
                logger.error(
                    f"Error processing price alert for {symbol} in chat {chat_id}: {e}",
                    exc_info=True)

    # Remove triggered alerts
    for chat_id_str, symbol in alerts_to_remove:
        if chat_id_str in tracked_stocks and symbol in tracked_stocks[
                chat_id_str]:
            del tracked_stocks[chat_id_str][symbol]
            if not tracked_stocks[chat_id_str]:
                del tracked_stocks[chat_id_str]
            save_tracked_stocks(tracked_stocks)
            logger.info(
                f"Removed triggered alert for {symbol} from chat {chat_id_str}."
            )


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets a price alert for a stock."""
    global tracked_stocks
    args = context.args
    chat_id = str(update.effective_chat.id)

    if len(args) != 3:
        await update.message.reply_text(
            "❗To set a price alert, please use the format: `/track <symbol> <price> <above/below>`\nExample: `/track GOOG 180 above` or `/track AMZN 170 below`",
            parse_mode="Markdown")
        return

    symbol = args[0].upper()
    try:
        target_price = float(args[1])
        if target_price <= 0:
            await update.message.reply_text(
                "The target price must be a positive number.")
            return
    except ValueError:
        await update.message.reply_text(
            "❗Invalid price. Please enter a numerical value for the target price."
        )
        return

    direction = args[2].lower()
    if direction not in ['above', 'below']:
        await update.message.reply_text(
            "❗Invalid direction. Please specify 'above' or 'below'.")
        return

    # Initialize chat_id entry if it doesn't exist
    if chat_id not in tracked_stocks:
        tracked_stocks[chat_id] = {}

    tracked_stocks[chat_id][symbol] = {
        'target_price': target_price,
        'direction': direction
    }
    save_tracked_stocks(tracked_stocks)

    await update.message.reply_text(
        f"✅ Great! I'll now alert you when *{symbol}*'s price goes *{direction} {target_price:.2f}*.",
        parse_mode="Markdown")
    logger.info(
        f"Added price alert for {symbol} {direction} {target_price} for chat {chat_id}."
    )


async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a price alert for a stock."""
    global tracked_stocks
    args = context.args
    chat_id = str(update.effective_chat.id)

    if len(args) != 1:
        await update.message.reply_text(
            "❗To stop tracking a stock, please use the format: `/untrack <symbol>`\nExample: `/untrack GOOG`",
            parse_mode="Markdown")
        return

    symbol = args[0].upper()

    if chat_id in tracked_stocks and symbol in tracked_stocks[chat_id]:
        del tracked_stocks[chat_id][symbol]
        if not tracked_stocks[
                chat_id]:  # If no more alerts for this chat, remove chat entry
            del tracked_stocks[chat_id]
        save_tracked_stocks(tracked_stocks)
        await update.message.reply_text(
            f"🗑️ Alright, I've stopped tracking *{symbol}* for you.",
            parse_mode="Markdown")
        logger.info(f"Removed price alert for {symbol} from chat {chat_id}.")
    else:
        await update.message.reply_text(
            f"🤔 Hmm, I don't seem to be tracking *{symbol}* for you. You can check your active alerts with `/myalerts`.",
            parse_mode="Markdown")


async def my_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows all active price alerts for the current chat."""
    chat_id = str(update.effective_chat.id)

    if chat_id not in tracked_stocks or not tracked_stocks[chat_id]:
        await update.message.reply_text(
            "✨ You currently have no active price alerts. Set one with `/track <symbol> <price> <above/below>`!",
            parse_mode="Markdown")
        return

    alerts_text = "🔔 *Your Active Price Alerts:*\n\n"
    for symbol, alert_data in tracked_stocks[chat_id].items():
        alerts_text += (
            f"• *{symbol}*: Alert me when price is *{alert_data['direction']} {alert_data['target_price']:.2f}*\n"
        )
    alerts_text += "\nTo remove an alert, use `/untrack <symbol>`."
    await update.message.reply_text(alerts_text, parse_mode="Markdown")


# --- Main function to run the bot ---
def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TELEGRAM_TOKEN).job_queue(
        JobQueue()).build()
    job_queue = application.job_queue

    # --- JOB QUEUE SETUP ---
    # Schedule the price alert checker to run every 60 seconds
    job_queue.run_repeating(
        check_price_alerts,  # This reference is now defined earlier
        interval=60,
        first=0,
        name="price_alert_checker")
    logger.info("Price alert checker job scheduled.")
    # --- END JOB QUEUE SETUP ---

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stock", stock))
    application.add_handler(CommandHandler("analyze", analyze))
    application.add_handler(CommandHandler("news", news))
    application.add_handler(CommandHandler("stocknews", stock_news))
    application.add_handler(CommandHandler("ask", ask))
    application.add_handler(CommandHandler("recommend", recommend))
    application.add_handler(CommandHandler("track", track))
    application.add_handler(CommandHandler("untrack", untrack))
    application.add_handler(CommandHandler("myalerts", my_alerts))

    # --- KEEP ALIVE ---
    Thread(target=keep_alive).start()
    logger.info("Keep-alive thread started.")
    # --- END KEEP ALIVE ---

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot started polling.")


if __name__ == "__main__":
    # Ensure your bot token is set before running
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN" or not TELEGRAM_TOKEN:
        logger.error(
            "🚫 ERROR: Please replace 'YOUR_BOT_TOKEN' in your .env file with your actual Telegram Bot Token from BotFather."
        )
    if not OPENAI_API_KEY:
        logger.warning(
            "⚠️ WARNING: OPENAI_API_KEY is not set in .env. AI features (/ask, /analyze summary, /recommend) will be disabled."
        )
    if not NEWS_API_KEY:
        logger.warning(
            "⚠️ WARNING: NEWS_API_KEY is not set in .env. General news (/news) will be disabled."
        )
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning(
            "⚠️ WARNING: ALPHA_VANTAGE_API_KEY is not set in .env. Detailed stock analysis (/analyze), stock news (/stocknews), and AI recommendations (/recommend) will be disabled."
        )

    if TELEGRAM_TOKEN:
        main()
    else:
        logger.critical(
            "Bot cannot start without TELEGRAM_TOKEN. Please set it in your .env file."
        )
