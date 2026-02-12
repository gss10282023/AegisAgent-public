package com.mas.clipboardreceipt;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;

public class MainActivity extends Activity {
  @Override
  protected void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    setContentView(R.layout.activity_main);

    final EditText edit = findViewById(R.id.editText);
    edit.requestFocus();
    edit.post(
        new Runnable() {
          @Override
          public void run() {
            InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            if (imm != null) {
              imm.showSoftInput(edit, InputMethodManager.SHOW_IMPLICIT);
            }
          }
        });
  }
}

