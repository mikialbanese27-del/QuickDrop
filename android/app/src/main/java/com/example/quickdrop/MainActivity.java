package com.example.quickdrop;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.os.*;
import android.view.*;
import android.widget.*;

public class MainActivity extends Activity {
    EditText host, token;
    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 45, 32, 24);
        TextView title = new TextView(this);
        title.setText("QuickDrop · Android"); title.setTextSize(24); layout.addView(title);
        TextView hint = new TextView(this);
        hint.setText("Collega il PC sulla stessa Wi-Fi. In seguito potrai inviare file da qualsiasi app usando Condividi → QuickDrop.");
        layout.addView(hint);
        host = new EditText(this); host.setSingleLine(); host.setHint("IP del PC e porta, es. 192.168.1.20:8765");
        host.setText(Net.address(this)); layout.addView(host);
        token = new EditText(this); token.setSingleLine(); token.setHint("Codice dal programma Windows");
        token.setText(Net.token(this)); layout.addView(token);
        Button save = new Button(this); save.setText("Salva e attiva ricezione"); layout.addView(save);
        save.setOnClickListener(v -> {
            String address = host.getText().toString().trim(), code = token.getText().toString().trim();
            if (!address.matches("[a-zA-Z0-9.:-]+") || code.isEmpty()) {
                Toast.makeText(this, "Inserisci indirizzo e codice validi", Toast.LENGTH_LONG).show(); return;
            }
            getSharedPreferences("quickdrop", 0).edit().putString("address", address).putString("token", code).apply();
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != getPackageManager().PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 42);
            }
            startReceiver();
            Toast.makeText(this, "Ricezione attivata", Toast.LENGTH_SHORT).show();
        });
        Button stop = new Button(this); stop.setText("Interrompi ricezione"); layout.addView(stop);
        stop.setOnClickListener(v -> { stopService(new Intent(this, ReceiverService.class)); Toast.makeText(this, "Ricezione interrotta", Toast.LENGTH_SHORT).show(); });
        TextView notice = new TextView(this);
        notice.setText("I file inviati dal PC arrivano in Download/QuickDrop. Android può limitare un servizio di ricezione prolungato per risparmio batteria.");
        layout.addView(notice);
        setContentView(layout);
    }
    private void startReceiver() {
        try { startForegroundService(new Intent(this, ReceiverService.class)); }
        catch (Exception e) { Toast.makeText(this, "Impossibile avviare: " + e.getMessage(), Toast.LENGTH_LONG).show(); }
    }
}
