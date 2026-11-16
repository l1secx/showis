from .base import SearchHit, SearchProvider
from .brave import BraveSearchProvider
from .connector import WebSearchConnector
from .cse import GoogleCseSearchProvider
from .duckduckgo import DuckDuckGoSearchProvider
from .hybrid import HybridSearchProvider
from .normalize import extract_platform_from_url, normalize_url
from .serpapi import SerpApiSearchProvider

__all__ = [
    "SearchHit",
    "SearchProvider",
    "BraveSearchProvider",
    "SerpApiSearchProvider",
    "GoogleCseSearchProvider",
    "DuckDuckGoSearchProvider",
    "HybridSearchProvider",
    "WebSearchConnector",
    "normalize_url",
    "extract_platform_from_url",
]
