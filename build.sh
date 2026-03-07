#!/bin/bash

# Start date
START_DATE=$(date +%s)

# Remove everything from userpatches/overlay
rm -rf userpatches/overlay/
mkdir -p userpatches/overlay

# Copy directory ip-terminal-code to userpatches/overlay
cp -r ip-terminal-code/* userpatches/overlay/

./compile.sh ip-terminal
# End date
END_DATE=$(date +%s)
# Calculate elapsed time in minutes and seconds
ELAPSED_TIME=$((END_DATE - START_DATE))
MINUTES=$((ELAPSED_TIME / 60))
SECONDS=$((ELAPSED_TIME % 60))
echo "Build completed in ${MINUTES} minutes and ${SECONDS} seconds."
