"""Event-driven signal engine for short-duration prediction markets (template)."""

from .bot import TradingBot
from .config import BotConfig

__all__ = ["BotConfig", "TradingBot"]
__version__ = "0.1.0"
