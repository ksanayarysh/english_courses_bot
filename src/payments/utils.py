# src/payments/utils.py

def get_currency_by_provider(provider: str) -> str:
    if provider == "mercadopago_pix" or  provider == "pix":
        return "BRL"
    return "RUB"
