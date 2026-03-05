#!/bin/bash
SD_PATH="/dev/mmcblk0"
IMG_XZ=$(find output/images/ -maxdepth 1 -name "Armbian*.img.xz" | head -1)
IMG=$(find output/images/ -maxdepth 1 -name "Armbian*.img" ! -name "*.img.xz" | head -1)

if [[ -n "$IMG_XZ" ]]; then
    echo "Found compressed image: ${IMG_XZ}"
    sudo umount "${SD_PATH}"* || true
    sudo wipefs -a "${SD_PATH}" || true
    echo "Writing image ${IMG_XZ} to ${SD_PATH}..."
    xzcat "$IMG_XZ" | sudo dd of="${SD_PATH}" bs=1M status=progress conv=fsync
    echo "Image written to ${SD_PATH}"
    sudo umount "${SD_PATH}"* || true
    echo "SD card is unmounted. You can now safely remove it."
elif [[ -n "$IMG" ]]; then
    echo "Found uncompressed image: ${IMG}"
    sudo umount "${SD_PATH}"* || true
    sudo wipefs -a "${SD_PATH}" || true
    echo "Writing image ${IMG} to ${SD_PATH}..."
    sudo dd if="$IMG" of="${SD_PATH}" bs=1M status=progress conv=fsync
    echo "Image written to ${SD_PATH}"
    sudo umount "${SD_PATH}"* || true
    echo "SD card is unmounted. You can now safely remove it."
else
    echo "No .img or .img.xz file found in output/images/"
    exit 1
fi
