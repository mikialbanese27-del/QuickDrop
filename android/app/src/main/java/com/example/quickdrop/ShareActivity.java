package com.example.quickdrop;

import android.app.Activity;
import android.content.*;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;
import java.util.ArrayList;

public class ShareActivity extends Activity {
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        if (!Net.configured(this)) {
            Toast.makeText(this, "Prima configura QuickDrop", Toast.LENGTH_LONG).show();
            startActivity(new Intent(this, MainActivity.class)); finish(); return;
        }
        Intent source = getIntent();
        ArrayList<Uri> files = new ArrayList<>();
        if (Intent.ACTION_SEND.equals(source.getAction())) {
            Uri uri = source.getParcelableExtra(Intent.EXTRA_STREAM);
            if (uri != null) files.add(uri);
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(source.getAction())) {
            ArrayList<Uri> uris = source.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (uris != null) files.addAll(uris);
        }
        if (files.isEmpty()) { Toast.makeText(this, "Nessun file condiviso", Toast.LENGTH_LONG).show(); finish(); return; }
        // No confirmation dialog. The user already chose QuickDrop in Android Sharesheet.
        Toast.makeText(this, "Invio di " + files.size() + " file al PC…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            int sent = 0;
            String error = null;
            for (Uri uri : files) {
                try { Net.upload(getApplicationContext(), uri); sent++; }
                catch (Exception e) { error = e.getMessage(); break; }
            }
            final int count = sent;
            final String msg = error;
            runOnUiThread(() -> {
                Toast.makeText(this, msg == null ? count + " file inviati" : count + " inviati; errore: " + msg, Toast.LENGTH_LONG).show();
                finish();
            });
        }).start();
    }
}
