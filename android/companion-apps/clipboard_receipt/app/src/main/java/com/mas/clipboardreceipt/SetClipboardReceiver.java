package com.mas.clipboardreceipt;

import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

public class SetClipboardReceiver extends BroadcastReceiver {
  private static final String TAG = "MasClipboardReceipt";

  @Override
  public void onReceive(Context context, Intent intent) {
    if (intent == null) {
      return;
    }
    if (!"com.mas.clipboardreceipt.SET_CLIP".equals(intent.getAction())) {
      return;
    }
    String text = intent.getStringExtra("text");
    if (text == null) {
      text = "";
    }

    try {
      ClipboardManager cm = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
      if (cm == null) {
        Log.w(TAG, "clipboard manager unavailable");
        return;
      }
      cm.setPrimaryClip(ClipData.newPlainText("mas", text));
      Log.i(TAG, "clipboard set via broadcast (len=" + text.length() + ")");
    } catch (Exception e) {
      Log.e(TAG, "failed to set clipboard via broadcast", e);
    }
  }
}

