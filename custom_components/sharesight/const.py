from datetime import timedelta

from homeassistant.const import Platform
SCAN_INTERVAL = timedelta(minutes=5)
UPDATE_SENSOR_SCAN_INTERVAL = timedelta(minutes=10)
PLATFORMS = [Platform.SENSOR]
DOMAIN = "sharesight"
REDIRECT_URL = 'urn:ietf:wg:oauth:2.0:oob'
API_VERSION = 'v2'
TOKEN_URL = 'https://api.sharesight.com/oauth2/token'
API_URL_BASE = 'https://api.sharesight.com/api/'
EDGE_TOKEN_URL = 'https://edge-api.sharesight.com/oauth2/token'
EDGE_API_URL_BASE = 'https://edge-api.sharesight.com/api/'
