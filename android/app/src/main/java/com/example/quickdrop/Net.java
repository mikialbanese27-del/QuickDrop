package com.example.quickdrop;

import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import org.json.JSONArray;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

final class Net {
    static String address(Context ctx) {
        return ctx.getSharedPreferences("quickdrop", 0).getString("address", "").trim();
    }
    static String token(Context ctx) {
        return ctx.getSharedPreferences("quickdrop", 0).getString("token", "").trim();
    }
    static boolean configured(Context ctx) { return !address(ctx).isEmpty() && !token(ctx).isEmpty(); }
    static HttpURLConnection connect(Context ctx, String endpoint, String method) throws IOException {
        String host = address(ctx);
        if (!host.matches("[a-zA-Z0-9.:-]+")) throw new IOException("Indirizzo PC non valido");
        URL url = new URL("http://" + host + endpoint);
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setConnectTimeout(4500);
        c.setReadTimeout(30000);
        c.setRequestMethod(method);
        c.setRequestProperty("X-QuickDrop-Token", token(ctx));
        c.setUseCaches(false);
        return c;
    }
    static String fileName(Context ctx, Uri uri) {
        String filename = null;
        try (Cursor cur = ctx.getContentResolver().query(uri, null, null, null, null)) {
            if (cur != null && cur.moveToFirst()) {
                int idx = cur.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (idx >= 0) filename = cur.getString(idx);
            }
        } catch (Exception ignored) { }
        if (filename == null || filename.isBlank()) filename = uri.getLastPathSegment();
        return filename == null || filename.isBlank() ? "file" : filename;
    }
    static void upload(Context ctx, Uri uri) throws IOException {
        // Pipe to a temporary cache file so the Content-Length is known (streaming the HTTP body).
        File temp = File.createTempFile("share_", ".bin", ctx.getCacheDir());
        try {
            try (InputStream in = ctx.getContentResolver().openInputStream(uri);
                 OutputStream out = new BufferedOutputStream(new FileOutputStream(temp))) {
                if (in == null) throw new IOException("File non leggibile");
                byte[] buf = new byte[256 * 1024];
                int n;
                while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            }
            HttpURLConnection c = connect(ctx, "/upload", "POST");
            String encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(fileName(ctx, uri).getBytes(StandardCharsets.UTF_8));
            c.setRequestProperty("X-File-Name", encoded);
            c.setDoOutput(true);
            c.setFixedLengthStreamingMode(temp.length());
            try {
                try (InputStream in = new BufferedInputStream(new FileInputStream(temp)); OutputStream out = c.getOutputStream()) {
                    byte[] buf = new byte[256 * 1024];
                    int n;
                    while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
                }
                if (c.getResponseCode() != 200) throw new IOException("Risposta PC: " + c.getResponseCode());
            } finally { c.disconnect(); }
        } finally { temp.delete(); }
    }
    static JSONArray pending(Context ctx) throws Exception {
        HttpURLConnection c = connect(ctx, "/pending", "GET");
        try {
            if (c.getResponseCode() != 200) throw new IOException("Ricezione: HTTP " + c.getResponseCode());
            try (InputStream in = c.getInputStream()) {
                return new JSONArray(new String(in.readAllBytes(), StandardCharsets.UTF_8));
            }
        } finally { c.disconnect(); }
    }
    static void ack(Context ctx, String id) throws IOException {
        HttpURLConnection c = connect(ctx, "/ack/" + id, "POST");
        try { if (c.getResponseCode() != 200) throw new IOException("ACK fallito"); }
        finally { c.disconnect(); }
    }
}
