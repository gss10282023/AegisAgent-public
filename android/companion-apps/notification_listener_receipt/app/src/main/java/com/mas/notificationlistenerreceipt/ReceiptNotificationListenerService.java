package com.mas.notificationlistenerreceipt;

import android.app.Notification;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.util.Log;

import org.json.JSONObject;
import org.json.JSONArray;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class ReceiptNotificationListenerService extends NotificationListenerService {
  private static final String TAG = "MasNotifReceipt";

  private static final Pattern TOKEN_RE =
      Pattern.compile("\\b[A-Za-z0-9]{2,}(?:_[A-Za-z0-9]{1,})+\\b");

  @Override
  public void onNotificationPosted(StatusBarNotification sbn) {
    try {
      Notification notif = sbn.getNotification();
      JSONObject entry = new JSONObject();

      String pkg = sbn.getPackageName();
      long postTime = sbn.getPostTime();

      String title = null;
      String text = null;
      if (notif != null && notif.extras != null) {
        CharSequence titleCs = notif.extras.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence textCs = notif.extras.getCharSequence(Notification.EXTRA_TEXT);
        if (titleCs != null) {
          title = titleCs.toString();
        }
        if (textCs != null) {
          text = textCs.toString();
        }
      }

      String tokenHit = extractToken(title, text);
      if (tokenHit == null) {
        tokenHit = "";
      }

      entry.put("pkg", pkg);
      entry.put("title", title == null ? JSONObject.NULL : title);
      entry.put("text", text == null ? JSONObject.NULL : text);
      entry.put("post_time", postTime);
      entry.put("token_hit", tokenHit);

      File dir = getExternalFilesDir(null);
      if (dir == null) {
        Log.w(TAG, "external files dir unavailable (receipt not written)");
        return;
      }
      File out = new File(dir, "notification_receipt.json");
      JSONArray arr = readExistingArray(out);
      arr.put(entry);
      pruneTail(arr, 50);
      atomicWriteUtf8(out, arr.toString());
      Log.i(TAG, "wrote receipt: " + out.getAbsolutePath() + " (count=" + arr.length() + ")");
    } catch (Exception e) {
      Log.e(TAG, "failed to write notification receipt", e);
    }
  }

  private static String extractToken(String... parts) {
    if (parts == null) {
      return null;
    }

    String best = null;
    boolean bestContainsTokenWord = false;

    for (String part : parts) {
      if (part == null) {
        continue;
      }
      Matcher m = TOKEN_RE.matcher(part);
      while (m.find()) {
        String cand = m.group();
        if (cand == null || cand.isEmpty()) {
          continue;
        }
        if (cand.length() < 6) {
          continue;
        }
        boolean hasTokenWord = cand.toUpperCase(Locale.ROOT).contains("TOKEN");
        if (best == null) {
          best = cand;
          bestContainsTokenWord = hasTokenWord;
          continue;
        }
        if (bestContainsTokenWord != hasTokenWord) {
          if (hasTokenWord) {
            best = cand;
            bestContainsTokenWord = true;
          }
          continue;
        }
        if (cand.length() > best.length()) {
          best = cand;
          bestContainsTokenWord = hasTokenWord;
        }
      }
    }

    return best;
  }

  private static void writeUtf8(File path, String text) throws Exception {
    File parent = path.getParentFile();
    if (parent != null && !parent.exists() && !parent.mkdirs()) {
      throw new IllegalStateException("failed to create parent dir: " + parent.getAbsolutePath());
    }
    byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
    try (FileOutputStream fos = new FileOutputStream(path, false)) {
      fos.write(bytes);
      fos.flush();
    }
  }

  private static JSONArray readExistingArray(File path) {
    if (!path.exists()) {
      return new JSONArray();
    }
    try {
      byte[] bytes = Files.readAllBytes(path.toPath());
      String text = new String(bytes, StandardCharsets.UTF_8).trim();
      if (text.isEmpty()) {
        return new JSONArray();
      }
      if (text.startsWith("[")) {
        return new JSONArray(text);
      }
    } catch (Exception ignored) {
      // Best-effort: corrupted/partial content -> start fresh.
    }
    return new JSONArray();
  }

  private static void pruneTail(JSONArray arr, int maxEntries) {
    if (maxEntries <= 0) {
      return;
    }
    int extra = arr.length() - maxEntries;
    if (extra <= 0) {
      return;
    }
    JSONArray trimmed = new JSONArray();
    for (int i = extra; i < arr.length(); i++) {
      trimmed.put(arr.opt(i));
    }
    while (arr.length() > 0) {
      arr.remove(arr.length() - 1);
    }
    for (int i = 0; i < trimmed.length(); i++) {
      arr.put(trimmed.opt(i));
    }
  }

  private static void atomicWriteUtf8(File path, String text) throws Exception {
    File parent = path.getParentFile();
    if (parent != null && !parent.exists() && !parent.mkdirs()) {
      throw new IllegalStateException("failed to create parent dir: " + parent.getAbsolutePath());
    }
    File tmp = new File(path.getAbsolutePath() + ".tmp");
    writeUtf8(tmp, text);
    if (!tmp.renameTo(path)) {
      // Fallback for filesystems where renameTo is flaky.
      writeUtf8(path, text);
      //noinspection ResultOfMethodCallIgnored
      tmp.delete();
    }
  }
}
