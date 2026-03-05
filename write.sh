#!/bin/bash
SD_PATH="/dev/mmcblk0"
IMG=$(find output/images/ -maxdepth 1 -name "Armbian*.img" | head -1)
if [[ -z "$IMG" ]]; then
    echo "No .img file found in output/images/"
    exit 1
fi
sudo umount "${SD_PATH}"* || true
sudo wipefs -a "${SD_PATH}" || true
echo "Writing image ${IMG} to ${SD_PATH}..."
sudo dd if="$IMG" of="${SD_PATH}" bs=1M status=progress conv=fsync
echo "Image written to ${SD_PATH}"
sudo umount "${SD_PATH}"* || true
echo "SD card is unmounted. You can now safely remove it."
