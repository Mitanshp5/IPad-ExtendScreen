# The pinned 9.3 SDK has missing libSystem re-exports; use 10.3 headers/stubs
# while retaining the actual iOS 9.3 minimum runtime. See docs/native-build-report.md.
TARGET := iphone:clang:10.3:9.3
DEBUG ?= 0
OPTFLAG := -Os
ARCHS := armv7
THEOS_PACKAGE_SCHEME :=
THEOS_PACKAGE_INSTALL_PREFIX :=
THEOS_PLATFORM_DEB_COMPRESSION_TYPE := gzip
include $(THEOS)/makefiles/common.mk

APPLICATION_NAME := ExtendScreen
ExtendScreen_FILES := Sources/main.m Sources/AppDelegate.m Sources/PSScreenViewController.m Sources/PSSettingsViewController.m Sources/PSKeyboardViewController.m Sources/PSConnectionClient.m Sources/PSStreamClient.m Sources/PSInputClient.m Sources/PSKeychainStore.m Sources/PSScreenPolicy.c Sources/PSCableServer.m
ExtendScreen_FRAMEWORKS := UIKit Foundation CoreGraphics Security ImageIO QuartzCore
ExtendScreen_CFLAGS := -fobjc-arc -Werror=unguarded-availability -Werror=implicit-function-declaration -Wno-deprecated-declarations
# Ad-hoc identity for this rootful jailbreak build, not an Apple provisioning profile.
# Keep Keychain access scoped to ExtendScreen; request no platform/root/debug privileges.
ExtendScreen_CODESIGN_FLAGS := -Sentitlements.plist
ExtendScreen_RESOURCE_DIRS := Resources
ExtendScreen_INSTALL_PATH := /Applications
include $(THEOS_MAKE_PATH)/application.mk

Package: local.extendscreen
Name: Extend Screen
Version: 1.0.5
Architecture: iphoneos-arm
Description: Extend your PC screen to an iPad mini (iOS 9.3.5) over Wi-Fi or USB cable.
Maintainer: Mitansh
Author: Mitansh
Section: Utilities
Depends: firmware (>= 9.3)