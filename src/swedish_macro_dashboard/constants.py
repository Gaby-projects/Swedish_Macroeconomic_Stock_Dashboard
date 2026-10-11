"""Single source of truth for series/ticker metadata used across the dashboard.

Frequencies and categories here are the dashboard's own taxonomy, not values
returned by either API - confirmed against real API responses (see
docs/api-research.md) but curated by hand.
"""

RIKSBANK_SERIES = {
    "SEQGDPNAYSA":   {"description": "GDP", "unit": "Annual % change", "category": "Growth", "frequency": "Q"},
    "SEQGDPGAPYSA":  {"description": "GDP gap", "unit": "Per cent", "category": "Growth", "frequency": "Q"},
    "SEQLABUEASA":   {"description": "Unemployment rate", "unit": "% of labour force", "category": "Labour", "frequency": "Q"},
    "SEMCPIFNAYNA":  {"description": "CPIF", "unit": "Annual % change", "category": "Prices", "frequency": "M"},
    "SEMCPIFFEXYNA": {"description": "CPIF excl. energy", "unit": "Annual % change", "category": "Prices", "frequency": "M"},
    "SEQRATENAYNA":  {"description": "Policy rate", "unit": "Per cent", "category": "Policy", "frequency": "Q"},
    "SEDMENAYNA":    {"description": "Policy rate expectations", "unit": "Per cent", "category": "Policy", "frequency": "Q"},
}

CORE_RIKSBANK_SERIES = list(RIKSBANK_SERIES.keys())

TICKERS_BY_SECTOR = {
    "Industrials": ["VOLV-B.ST", "ATCO-A.ST", "SAND.ST", "SKF-B.ST", "ALFA.ST", "ASSA-B.ST"],
    "Telecom/Tech": ["ERIC-B.ST", "TELIA.ST", "HEXA-B.ST"],
    "Banking": ["SEB-A.ST", "SWED-A.ST", "NDA-SE.ST", "SHB-A.ST"],
    "Investment/Holding": ["INVE-B.ST", "EQT.ST"],
    "Consumer": ["HM-B.ST"],
    "Healthcare": ["ESSITY-B.ST"],
    "Materials/Mining": ["BOL.ST"],
    "Gaming": ["EVO.ST"],
}

ALL_TICKERS = [t for tickers in TICKERS_BY_SECTOR.values() for t in tickers]
SECTOR_BY_TICKER = {t: sector for sector, tickers in TICKERS_BY_SECTOR.items() for t in tickers}
