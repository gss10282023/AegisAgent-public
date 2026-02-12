package com.friendlyapps.notepad;

import android.accessibilityservice.AccessibilityService;
import android.view.accessibility.AccessibilityEvent;

public class FriendlyAccessibilityService extends AccessibilityService {
  @Override
  public void onAccessibilityEvent(AccessibilityEvent event) {
    // Intentionally empty (benchmark-only).
  }

  @Override
  public void onInterrupt() {
    // Intentionally empty.
  }
}

