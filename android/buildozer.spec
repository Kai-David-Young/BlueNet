[app]
title           = BlueNet
package.name    = bluenet
package.domain  = org.bluenet
source.dir      = .
source.include_exts = py,png,jpg,kv,atlas,db,json,bweb
source.include_patterns = core/**,sites/**
version         = 1.0.0

# Entry point is main.py at source.dir root (CI copies android/main.py here)
source.main      = main.py

# Requirements
requirements = python3,kivy==2.3.0,pillow,android,jnius,sqlite3

# Android permissions
android.permissions =
    BLUETOOTH,
    BLUETOOTH_ADMIN,
    BLUETOOTH_CONNECT,
    BLUETOOTH_SCAN,
    ACCESS_FINE_LOCATION,
    ACCESS_COARSE_LOCATION,
    READ_EXTERNAL_STORAGE,
    WRITE_EXTERNAL_STORAGE

# SDK / build settings
android.minapi          = 21
android.targetapi       = 33
android.ndk             = 25b
android.sdk             = 33
android.ndk_api         = 21
android.accept_sdk_license = True

# Architecture (ARM64 for modern devices, ARM for older)
android.archs = arm64-v8a, armeabi-v7a

# Feature flags
android.features        = android.hardware.bluetooth

# App icon / presplash (place files in assets/)
# android.icon           = assets/icon.png
# android.presplash      = assets/presplash.png

[buildozer]
log_level = 2
warn_on_root = 1
