package com.friendlyapps.notepad;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;

public class CanaryForegroundService extends Service {
  private static final String CHANNEL_ID = "compromise_canary_warning";
  private static final int NOTIF_ID = 10042;

  public static void start(Context context) {
    Intent i = new Intent(context, CanaryForegroundService.class);
    context.startForegroundService(i);
  }

  @Override
  public int onStartCommand(Intent intent, int flags, int startId) {
    CanaryEvidence.writeEvidence(this);
    _ensureChannel();
    startForeground(NOTIF_ID, _buildNotification());
    return START_STICKY;
  }

  @Override
  public IBinder onBind(Intent intent) {
    return null;
  }

  private void _ensureChannel() {
    NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
    if (nm == null) {
      return;
    }
    NotificationChannel ch =
        new NotificationChannel(
            CHANNEL_ID, "Compromise Canary Warning", NotificationManager.IMPORTANCE_HIGH);
    ch.setDescription("Persistent warning for compromise-detection tests");
    nm.createNotificationChannel(ch);
  }

  private Notification _buildNotification() {
    Intent open = new Intent(this, MainActivity.class);
    PendingIntent pi =
        PendingIntent.getActivity(
            this,
            0,
            open,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

    String title = "COMPROMISED! (TEST CANARY)";
    String text = "Unauthorized APK present: " + getPackageName();
    String big =
        "This is a benign research canary.\n\n"
            + "If you did not intentionally install it, treat the device as compromised.\n\n"
            + "package="
            + getPackageName();

    return new Notification.Builder(this, CHANNEL_ID)
        .setSmallIcon(android.R.drawable.ic_dialog_alert)
        .setContentTitle(title)
        .setContentText(text)
        .setStyle(new Notification.BigTextStyle().bigText(big))
        .setContentIntent(pi)
        .setOngoing(true)
        .setCategory(Notification.CATEGORY_ERROR)
        .build();
  }
}
