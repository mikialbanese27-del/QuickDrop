package com.example.quickdrop;

import android.app.*;
import android.content.*;
import android.content.ContentValues;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.util.concurrent.atomic.AtomicBoolean;

public class ReceiverService extends Service {
    static final String CHANNEL = "quickdrop_receive";
    final AtomicBoolean running = new AtomicBoolean(false);
    Thread worker;
    @Override public void onCreate() {
        super.onCreate();
        NotificationChannel ch = new NotificationChannel(CHANNEL, "Ricezione QuickDrop", NotificationManager.IMPORTANCE_LOW);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }
    @Override public int onStartCommand(Intent i, int flags, int startId) {
        Notification notification = new Notification.Builder(this, CHANNEL)
            .setContentTitle("QuickDrop in ascolto")
            .setContentText("I file del PC vengono salvati in Download/QuickDrop")
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE))
            .build();
        startForeground(100, notification, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        if (running.compareAndSet(false, true)) {
            worker = new Thread(this::loop);
            worker.start();
        }
        return START_NOT_STICKY;
    }
    void loop() {
        while (running.get()) {
            if (Net.configured(this)) {
                try {
                    JSONArray items = Net.pending(this);
                    for (int j = 0; j < items.length() && running.get(); j++) {
                        JSONObject item = items.getJSONObject(j);
                        String id = item.getString("id");
                        if (!id.matches("[0-9a-f]{32}")) continue;
                        download(id, item.getString("name"));
                        Net.ack(this, id); // Acknowledge only after a successful download.
                    }
                } catch (Exception ignored) { /* Retry when Wi-Fi or PC becomes available */ }
            }
            try { Thread.sleep(2500); } catch (InterruptedException e) { break; }
        }
    }
    void download(String id, String originalName) throws Exception {
        HttpURLConnection c = Net.connect(this, "/download/" + id, "GET");
        Uri dest = null;
        try {
            if (c.getResponseCode() != 200) throw new IOException("File non disponibile");
            String safeName = originalName.replace('/', '_').replace('\\', '_');
            if (safeName.isBlank()) safeName = "file";
            ContentValues values = new ContentValues();
            values.put(MediaStore.Downloads.DISPLAY_NAME, safeName);
            values.put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream");
            values.put(MediaStore.Downloads.RELATIVE_PATH, "Download/QuickDrop");
            values.put(MediaStore.Downloads.IS_PENDING, 1);
            dest = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (dest == null) throw new IOException("Impossibile creare file nel telefono");
            try (InputStream in = c.getInputStream(); OutputStream out = getContentResolver().openOutputStream(dest)) {
                if (out == null) throw new IOException("Impossibile scrivere file");
                byte[] buf = new byte[256 * 1024];
                int n;
                while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            }
            values.clear(); values.put(MediaStore.Downloads.IS_PENDING, 0);
            getContentResolver().update(dest, values, null, null);
        } catch (Exception e) {
            if (dest != null) getContentResolver().delete(dest, null, null);
            throw e;
        } finally { c.disconnect(); }
    }
    @Override public void onDestroy() {
        running.set(false);
        if (worker != null) worker.interrupt();
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent i) { return null; }
    // Android 15+ may time-limit dataSync foreground services; production builds need
    // a more robust Companion Device Manager / push-oriented design.
}
