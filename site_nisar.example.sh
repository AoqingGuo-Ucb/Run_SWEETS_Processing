# Copy to site_nisar.sh and edit for the real AOI and available NISAR GSLC dates.
SITE="Prima_NISAR"
WEST=-117.646
SOUTH=33.4704
EAST=-117.5842
NORTH=33.5084
START_DATE="2026-01-01"
END_DATE="2026-09-26"
# Leave empty for discovery, or set verified NISAR values (not Sentinel-1 values).
NISAR_TRACK=""
NISAR_FRAME=""
NISAR_FREQUENCY="A"
NISAR_POLARIZATION="HH"
# Applies when creating a new configuration. Use a new SITE to switch collections.
NISAR_COLLECTION="NISAR_L2_GSLC_PROVISIONAL_V1"
# For early pre-calibration products: NISAR_COLLECTION="NISAR_L2_GSLC_BETA_V1"
SWEETS_STRIDES=(1 1)
SWEETS_REPO="$HOME/Bhaltos/AoqingShare/sfw/sweets"
# Empty: search SWEETS_REPO first, then the shell script directory.
# Alternatively set the absolute path to run_nisar_checked.py anywhere else.
NISAR_CHECK_SCRIPT=""
PROJECT_ROOT="$HOME/Bhaltos/AoqingShare/NISAR_Projects"
