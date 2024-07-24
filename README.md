# Home Assistant Sharesight integration
![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)

## Services
This allows you to monitor your sharesight portfolio value, currently supports:
- Payout Gain
- Currency Gain
- Total Gain Percent
- Total Gain
- Portfolio Value


## Installation using HACS

1. Use [HACS](https://hacs.xyz/docs/setup/download), in `HACS > Integrations > Hamburger Menu > Custom Repositories add https://github.com/Poshy163/HomeAssistant-Sharesight with category set to integration.
2. in `HACS > Integrations > Explore & Add Repositories` search for "Sharesight". 
3. Restart Home Assistant.
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Sharesight".
7. You will be prompted for the API details to your Sharesight Account

## Manual Installation

1. Make a custom_components/sharesight folder in your Home Assistant file system.
2. Copy all the files and folders from this repository into that custom_components/sharesight folder
3. Restart Home Assistant
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. Setup this integration via `Configuration -> Integrations -> Add -> Sharesight`
7. You will be prompted for the API details to your Sharesight Account