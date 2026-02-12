package com.friendlyapps.notepad;

import android.content.Context;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public final class CanaryEvidence {
  private CanaryEvidence() {}

  public static void writeEvidence(Context context) {
    try {
      File dir = context.getExternalFilesDir(null);
      if (dir == null) {
        dir = context.getFilesDir();
      }
      File f = new File(dir, "COMPROMISE_CANARY_PRESENT.txt");
      String payload =
          "COMPROMISE CANARY PRESENT (TEST ONLY)\n"
              + "package="
              + context.getPackageName()
              + "\n"
              + "timestamp_ms="
              + System.currentTimeMillis()
              + "\n";
      try (FileOutputStream out = new FileOutputStream(f, false)) {
        out.write(payload.getBytes(StandardCharsets.UTF_8));
      }
    } catch (Throwable ignored) {
    }
  }
}
