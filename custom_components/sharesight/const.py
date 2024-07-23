from homeassistant.const import Platform

PLATFORMS = [Platform.SENSOR]
DOMAIN = "sharesight"
REDIRECT_URL = 'urn:ietf:wg:oauth:2.0:oob'
TOKEN_URL = 'https://api.sharesight.com/oauth2/token'
API_URL_BASE = 'https://api.sharesight.com/api/'
API_VERSION = 'v2'
PORTFOLIO_ID = ''
