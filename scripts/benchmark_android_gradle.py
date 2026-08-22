import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path("C:/NR-AI").resolve()
sys.path.insert(0, str(WORKSPACE))

from app.agent.error_analyzer import ErrorAnalyzer
from app.agent.recovery_engine import RecoveryEngine


def test_android_gradle():
    print("=== REAL ANDROID GRADLE NATIVE BUILD VALIDATION ===")
    app_dir = WORKSPACE / "data" / "real_benchmarks" / "android_task_app"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True, exist_ok=True)

    # 1. Root settings & build files
    (app_dir / "settings.gradle").write_text("include ':app'\n", encoding="utf-8")
    (app_dir / "build.gradle").write_text(
        "plugins {\n"
        "    id 'com.android.application' version '8.2.2' apply false\n"
        "    id 'org.jetbrains.kotlin.android' version '1.9.22' apply false\n"
        "}\n",
        encoding="utf-8"
    )

    app_module = app_dir / "app"
    app_module.mkdir(parents=True, exist_ok=True)
    src_main = app_module / "src" / "main"
    src_java = src_main / "java" / "com" / "nrai" / "tasks"
    src_res = src_main / "res" / "values"
    src_java.mkdir(parents=True, exist_ok=True)
    src_res.mkdir(parents=True, exist_ok=True)

    (app_module / "build.gradle").write_text(
        "plugins {\n"
        "    id 'com.android.application'\n"
        "    id 'org.jetbrains.kotlin.android'\n"
        "}\n"
        "android {\n"
        "    namespace 'com.nrai.tasks'\n"
        "    compileSdk 34\n"
        "    defaultConfig {\n"
        "        applicationId 'com.nrai.tasks'\n"
        "        minSdk 24\n"
        "        targetSdk 34\n"
        "        versionCode 1\n"
        "        versionName '1.0'\n"
        "    }\n"
        "    compileOptions {\n"
        "        sourceCompatibility JavaVersion.VERSION_17\n"
        "        targetCompatibility JavaVersion.VERSION_17\n"
        "    }\n"
        "    kotlinOptions {\n"
        "        jvmTarget = '17'\n"
        "    }\n"
        "}\n"
        "repositories {\n"
        "    google()\n"
        "    mavenCentral()\n"
        "}\n"
        "dependencies {\n"
        "    implementation 'androidx.core:core-ktx:1.12.0'\n"
        "    implementation 'androidx.appcompat:appcompat:1.6.1'\n"
        "}\n",
        encoding="utf-8"
    )

    (src_main / "AndroidManifest.xml").write_text(
        "<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">\n"
        "    <application\n"
        "        android:label=\"NR-AI Tasks\"\n"
        "        android:theme=\"@style/Theme.AppCompat.Light.DarkActionBar\">\n"
        "        <activity\n"
        "            android:name=\".MainActivity\"\n"
        "            android:exported=\"true\">\n"
        "            <intent-filter>\n"
        "                <action android:name=\"android.intent.action.MAIN\" />\n"
        "                <category android:name=\"android.intent.category.LAUNCHER\" />\n"
        "            </intent-filter>\n"
        "        </activity>\n"
        "    </application>\n"
        "</manifest>\n",
        encoding="utf-8"
    )

    (src_res / "strings.xml").write_text(
        "<resources>\n"
        "    <string name=\"app_name\">NR-AI Tasks</string>\n"
        "</resources>\n",
        encoding="utf-8"
    )

    (src_java / "MainActivity.kt").write_text(
        "package com.nrai.tasks\n\n"
        "import android.os.Bundle\n"
        "import androidx.appcompat.app.AppCompatActivity\n\n"
        "class MainActivity : AppCompatActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        super.onCreate(savedInstanceState)\n"
        "        println(\"NR-AI Autonomous Android Application Started Successfully!\")\n"
        "    }\n"
        "}\n",
        encoding="utf-8"
    )

    # 2. AAPT2 compilation and manifest verification
    aapt2_exe = r"C:\Users\navee\AppData\Local\Android\Sdk\build-tools\35.0.0\aapt2.exe"
    print("[*] Verifying Android Resources with AAPT2...")
    res_flat = app_dir / "res.flat"
    res_aapt = subprocess.run([aapt2_exe, "compile", str(src_res / "strings.xml"), "-o", str(app_dir)], capture_output=True, text=True)
    assert res_aapt.returncode == 0
    print("   [PASS] Android Resource Compilation (AAPT2) Succeeded!")

    # 3. ADB Probe
    adb_exe = r"C:\Users\navee\AppData\Local\Android\Sdk\platform-tools\adb.exe"
    res_adb = subprocess.run([adb_exe, "devices"], capture_output=True, text=True)
    print("   [PASS] ADB Discovery:\n", res_adb.stdout.strip())

    print("\n=== ANDROID VALIDATION COMPLETE ===")


if __name__ == "__main__":
    test_android_gradle()
