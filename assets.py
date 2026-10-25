"""Seed catalogue from the prior project. Not a verified complete market list.
The live feed adapter can supplement symbols from updateAssets.
"""

OTC_ASSETS = [
    ("AUD/CAD OTC", "AUDCAD_otc"),
    ("AUD/CHF OTC", "AUDCHF_otc"),
    ("AUD/JPY OTC", "AUDJPY_otc"),
    ("AUD/NZD OTC", "AUDNZD_otc"),
    ("AUD/USD OTC", "AUDUSD_otc"),
    ("CAD/CHF OTC", "CADCHF_otc"),
    ("CAD/JPY OTC", "CADJPY_otc"),
    ("CHF/JPY OTC", "CHFJPY_otc"),
    ("EUR/AUD OTC", "EURAUD_otc"),
    ("EUR/CAD OTC", "EURCAD_otc"),
    ("EUR/CHF OTC", "EURCHF_otc"),
    ("EUR/GBP OTC", "EURGBP_otc"),
    ("EUR/JPY OTC", "EURJPY_otc"),
    ("EUR/NZD OTC", "EURNZD_otc"),
    ("EUR/USD OTC", "EURUSD_otc"),
    ("GBP/AUD OTC", "GBPAUD_otc"),
    ("GBP/CAD OTC", "GBPCAD_otc"),
    ("GBP/CHF OTC", "GBPCHF_otc"),
    ("GBP/JPY OTC", "GBPJPY_otc"),
    ("GBP/NZD OTC", "GBPNZD_otc"),
    ("GBP/USD OTC", "GBPUSD_otc"),
    ("NZD/JPY OTC", "NZDJPY_otc"),
    ("NZD/USD OTC", "NZDUSD_otc"),
    ("USD/BRL OTC", "USDBRL_otc"),
    ("USD/CAD OTC", "USDCAD_otc"),
    ("USD/CHF OTC", "USDCHF_otc"),
    ("USD/INR OTC", "USDINR_otc"),
    ("USD/JPY OTC", "USDJPY_otc"),
    ("USD/MXN OTC", "USDMXN_otc"),
    ("USD/RUB OTC", "USDRUB_otc"),
    ("USD/SGD OTC", "USDSGD_otc"),
    ("USD/TRY OTC", "USDTRY_otc"),
    ("USD/ZAR OTC", "USDZAR_otc"),
]

# Fast lookup helpers
BY_SYMBOL = {sym: name for name, sym in OTC_ASSETS}
BY_NAME = {name: sym for name, sym in OTC_ASSETS}
