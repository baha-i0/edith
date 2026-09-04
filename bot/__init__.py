"""EDITH trading bot - Binance USD-M Futures.

Modul haritasi:
  config.py     -> ayarlar + sert dogrulama
  indicators.py -> saf Python gostergeler
  strategy.py   -> sinyal uretimi ve pozisyon yonetim kurallari
  risk.py       -> pozisyon boyutu, gunluk limitler, soguma
  backtest.py   -> kotumser backtest motoru
  engine.py     -> canli/paper dongu
  exchange/     -> Binance REST, paper broker, live broker
"""

__version__ = "1.0.0"
