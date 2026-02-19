from datetime import timedelta

from homeassistant.const import Platform

SCAN_INTERVAL = timedelta(minutes=5)
UPDATE_SENSOR_SCAN_INTERVAL = timedelta(minutes=10)
PLATFORMS = [Platform.SENSOR]
DOMAIN = "sharesight"

CONF_PORTFOLIO_ID = "portfolio_id"
CONF_USE_EDGE = "use_edge_url"

APP_VERSION = "v2"

AUTHORIZATION_URL = "https://api.sharesight.com/oauth2/authorize"
TOKEN_URL = "https://api.sharesight.com/oauth2/token"
API_URL_BASE = "https://api.sharesight.com/api/"

EDGE_TOKEN_URL = "https://edge-api.sharesight.com/oauth2/token"
EDGE_API_URL_BASE = "https://edge-api.sharesight.com/api/"
