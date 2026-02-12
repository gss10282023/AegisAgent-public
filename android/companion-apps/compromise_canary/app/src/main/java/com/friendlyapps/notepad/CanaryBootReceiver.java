package com.friendlyapps.notepad;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class CanaryBootReceiver extends BroadcastReceiver {
  @Override
  public void onReceive(Context context, Intent intent) {
    if (intent == null) {
      return;
    }
    String action = intent.getAction();
    if (Intent.ACTION_BOOT_COMPLETED.equals(action)) {
      CanaryForegroundService.start(context);
    }
  }
}
