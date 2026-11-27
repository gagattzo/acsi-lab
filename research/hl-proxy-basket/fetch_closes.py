"""Download daily closes from Yahoo Finance into yahoo_closes.csv (research use only, not republishable, gitignored).
Needs yfinance (requirements-stack.txt)."""
import yfinance as yf

TICKERS = ['ORCL', 'CRWV', 'NBIS', 'IREN', 'CIFR', 'META', 'NVDA', 'MSFT', 'BX', 'QQQ', 'SMH', 'TLT', 'BTC-USD']
px = yf.download(TICKERS, start='2019-01-01', auto_adjust=True, progress=False)['Close']
px.to_csv('yahoo_closes.csv')
print(px.shape, px.index.min().date(), px.index.max().date())
