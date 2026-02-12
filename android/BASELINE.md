# Android Baseline (to be pinned)

Fill this file when Android emulator integration is introduced.

## Emulator
- emulator version: 36.1.9.0 (build_id 13823996)
- avd name: Pixel_9

## System image
- system image package (sdkmanager id): system-images;android-36;google_apis_playstore;arm64-v8a
- build fingerprint (`adb shell getprop ro.build.fingerprint`): google/sdk_gphone64_arm64/emu64a:16/BP22.250325.006/13344233:user/release-keys
- system image directory sha256 (see docs/governance/REPRODUCIBILITY.md): db2699efbeafea3554baa92bbcd0f424eeeaf0c78b3dafb1bf0a38be0a17d54d

## AVD config
- config.ini: ~/.android/avd/Pixel_9.avd/config.ini
- display: 1080x2424 @ 420dpi (portrait), skin=pixel_9, showDeviceFrame=yes
- input method: com.droidrun.portal/.input.DroidrunKeyboardIME

## Snapshots
- baseline (no canary installed): baseline_clean_no_canary
- canary preinstalled (optional): with_friendly_notepad_canary
