from datetime import timedelta

from homeassistant.const import Platform
SCAN_INTERVAL = timedelta(minutes=5)
PLATFORMS = [Platform.SENSOR]
DOMAIN = "sharesight"
REDIRECT_URL = 'urn:ietf:wg:oauth:2.0:oob'
TOKEN_URL = 'https://api.sharesight.com/oauth2/token'
API_URL_BASE = 'https://api.sharesight.com/api/'
API_VERSION = 'v2'
PORTFOLIO_ID = ''


async def set_portfolio_id(portfolio_num):
    global PORTFOLIO_ID
    PORTFOLIO_ID = portfolio_num


async def get_portfolio_id():
    return PORTFOLIO_ID
