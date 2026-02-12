package com.mas.clipboardreceipt;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.inputmethodservice.InputMethodService;
import android.util.Log;
import android.view.inputmethod.EditorInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class ReceiptImeService extends InputMethodService {
  private static final String TAG = "MasClipboardReceipt";
  private static final Pattern TOKEN_RE =
      Pattern.compile("\\b[A-Za-z0-9]{2,}(?:_[A-Za-z0-9]{1,})+\\b");

  private ClipboardManager clipboard;
  private ClipboardManager.OnPrimaryClipChangedListener listener;

  @Override
  public void onCreate() {
    super.onCreate();
    clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
    if (clipboard == null) {
      Log.w(TAG, "clipboard manager unavailable (listener not installed)");
      return;
    }

    listener =
        new ClipboardManager.OnPrimaryClipChangedListener() {
          @Override
          public void onPrimaryClipChanged() {
            handleClipboardChanged();
          }
        };
    clipboard.addPrimaryClipChangedListener(listener);
    Log.i(TAG, "clipboard listener installed");
  }

  @Override
  public void onDestroy() {
    if (clipboard != null && listener != null) {
      try {
        clipboard.removePrimaryClipChangedListener(listener);
      } catch (Exception ignored) {
      }
    }
    super.onDestroy();
  }

  private void handleClipboardChanged() {
    try {
      if (clipboard == null) {
        return;
      }
      ClipData clip = clipboard.getPrimaryClip();
      if (clip == null || clip.getItemCount() <= 0) {
        return;
      }

      CharSequence cs = clip.getItemAt(0).coerceToText(this);
      if (cs == null) {
        return;
      }
      String text = cs.toString();

      long setTime = System.currentTimeMillis();
      String token = extractToken(text);
      if (token == null || token.isEmpty()) {
        token = text;
      }

      String sourcePkg = null;
      EditorInfo info = getCurrentInputEditorInfo();
      if (info != null) {
        sourcePkg = info.packageName;
      }

      JSONObject entry = new JSONObject();
      entry.put("set_time", setTime);
      entry.put("token", token);
      entry.put("source_pkg", sourcePkg == null ? JSONObject.NULL : sourcePkg);
      entry.put("text", text);

      File dir = getExternalFilesDir(null);
      if (dir == null) {
        Log.w(TAG, "external files dir unavailable (receipt not written)");
        return;
      }
      File out = new File(dir, "clipboard_receipt.json");

      JSONArray arr = readExistingArray(out);
      arr.put(entry);
      pruneTail(arr, 50);
      atomicWriteUtf8(out, arr.toString());

      Log.i(TAG, "wrote receipt: " + out.getAbsolutePath() + " (count=" + arr.length() + ")");
    } catch (Exception e) {
      Log.e(TAG, "failed to write clipboard receipt", e);
    }
  }

  private static String extractToken(String text) {
    if (text == null) {
      return null;
    }
    String best = null;
    boolean bestContainsTokenWord = false;

    Matcher m = TOKEN_RE.matcher(text);
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
      writeUtf8(path, text);
      //noinspection ResultOfMethodCallIgnored
      tmp.delete();
    }
  }
}

